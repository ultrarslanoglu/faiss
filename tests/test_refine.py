# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.


import numpy as np
import unittest
import faiss

from faiss.contrib import datasets, evaluation


class TestDistanceComputer(unittest.TestCase):

    def do_test(self, factory_string, metric_type=faiss.METRIC_L2):
        ds = datasets.SyntheticDataset(32, 1000, 200, 20)

        index = faiss.index_factory(32, factory_string, metric_type)
        index.train(ds.get_train())
        index.add(ds.get_database())
        xq = ds.get_queries()
        Dref, Iref = index.search(xq, 10)

        for is_FlatCodesDistanceComputer in False, True:
            if not is_FlatCodesDistanceComputer:
                dc = index.get_distance_computer()
            else:
                if not isinstance(index, faiss.IndexFlatCodes):
                    continue
                dc = index.get_FlatCodesDistanceComputer()
            self.assertTrue(dc.this.own())
            for q in range(ds.nq):
                dc.set_query(faiss.swig_ptr(xq[q]))
                for j in range(10):
                    ref_dis = Dref[q, j]
                    new_dis = dc(int(Iref[q, j]))
                    np.testing.assert_almost_equal(
                        new_dis, ref_dis, decimal=5)

    def test_distance_computer_PQ(self):
        self.do_test("PQ8np")

    def test_distance_computer_SQ(self):
        self.do_test("SQ8")

    def test_distance_computer_SQ6(self):
        self.do_test("SQ6")

    def test_distance_computer_PQbit6(self):
        self.do_test("PQ8x6np")

    def test_distance_computer_PQbit6_ip(self):
        self.do_test("PQ8x6np", faiss.METRIC_INNER_PRODUCT)

    def test_distance_computer_VT(self):
        self.do_test("PCA20,SQ8")

    def test_distance_computer_AQ_decompress(self):
        self.do_test("RQ3x4")    # test decompress path

    def test_distance_computer_AQ_LUT(self):
        self.do_test("RQ3x4_Nqint8")    # test LUT path

    def test_distance_computer_AQ_LUT_IP(self):
        self.do_test("RQ3x4_Nqint8", faiss.METRIC_INNER_PRODUCT)


class TestIndexRefineSearchParams(unittest.TestCase):

    def do_test(self, factory_string):
        ds = datasets.SyntheticDataset(32, 256, 100, 40)

        index = faiss.index_factory(32, factory_string)
        index.train(ds.get_train())
        index.add(ds.get_database())
        index.nprobe = 4
        xq = ds.get_queries()

        # do a search with k_factor = 1
        D1, I1 = index.search(xq, 10)
        inter1 = faiss.eval_intersection(I1, ds.get_groundtruth(10))

        # do a search with k_factor = 1.5
        params = faiss.IndexRefineSearchParameters(k_factor=1.1)
        D2, I2 = index.search(xq, 10, params=params)
        inter2 = faiss.eval_intersection(I2, ds.get_groundtruth(10))

        # do a search with k_factor = 2
        params = faiss.IndexRefineSearchParameters(k_factor=2)
        D3, I3 = index.search(xq, 10, params=params)
        inter3 = faiss.eval_intersection(I3, ds.get_groundtruth(10))

        # make sure that the recall rate increases with k_factor
        self.assertGreater(inter2, inter1)
        self.assertGreater(inter3, inter2)

        # make sure that the baseline k_factor is unchanged
        self.assertEqual(index.k_factor, 1)

        # try passing params for the baseline index, change nprobe
        base_params = faiss.IVFSearchParameters(nprobe=10)
        params = faiss.IndexRefineSearchParameters(k_factor=1, base_index_params=base_params)
        D4, I4 = index.search(xq, 10, params=params)
        inter4 = faiss.eval_intersection(I4, ds.get_groundtruth(10))

        base_params = faiss.IVFSearchParameters(nprobe=2)
        params = faiss.IndexRefineSearchParameters(k_factor=1, base_index_params=base_params)
        D5, I5 = index.search(xq, 10, params=params)
        inter5 = faiss.eval_intersection(I5, ds.get_groundtruth(10))

        # make sure that the recall rate changes
        self.assertNotEqual(inter4, inter5)

    def test_rflat(self):
        # flat is handled by the IndexRefineFlat class
        self.do_test("IVF8,PQ2x4np,RFlat")

    def test_refine_sq8(self):
        # this case uses the IndexRefine class
        self.do_test("IVF8,PQ2x4np,Refine(SQ8)")


class TestIndexRefineRangeSearch(unittest.TestCase):

    def do_test(self, factory_string):
        d = 32
        radius = 8

        ds = datasets.SyntheticDataset(d, 1024, 512, 256)

        index = faiss.index_factory(d, factory_string)
        index.train(ds.get_train())
        index.add(ds.get_database())
        xq = ds.get_queries()
        xb = ds.get_database()

        # perform a range_search
        lims_1, D1, I1 = index.range_search(xq, radius)

        # create a baseline (FlatL2)
        index_flat = faiss.IndexFlatL2(d)
        index_flat.train(ds.get_train())
        index_flat.add(ds.get_database())

        lims_ref, Dref, Iref = index_flat.range_search(xq, radius)

        # add a refine index on top of the index
        index_r = faiss.IndexRefine(index, index_flat)
        lims_2, D2, I2 = index_r.range_search(xq, radius)

        # validate: refined range_search() keeps indices untouched
        precision_1, recall_1 = evaluation.range_PR(lims_ref, Iref, lims_1, I1)

        precision_2, recall_2 = evaluation.range_PR(lims_ref, Iref, lims_2, I2)

        self.assertAlmostEqual(recall_1, recall_2)

        # validate: refined range_search() updates distances, and new distances are correct L2 distances
        for iq in range(0, ds.nq):
            start_lim = lims_2[iq]
            end_lim = lims_2[iq + 1]
            for i_lim in range(start_lim, end_lim):
                idx = I2[i_lim]
                l2_dis = np.sum(np.square(xq[iq : iq + 1,] - xb[idx : idx + 1,]))

                self.assertAlmostEqual(l2_dis, D2[i_lim], places=4)


    def test_refine_1(self):
        self.do_test("SQ4")
