# Author: Leland McInnes <leland.mcinnes@gmail.com>
#
# License: BSD 2 clause

import numba
import numpy as np


@numba.njit('i4(i8[:])')
def tau_rand_int(state):
    """A fast (pseudo)-random number generator.

    Parameters
    ----------
    state: array of int64, shape (3,)
        The internal state of the rng

    Returns
    -------
    A (pseudo)-random int32 value
    """
    state[0] = (((state[0] & 4294967294) << 12) & 0xffffffff) ^ \
               ((((state[0] << 13) & 0xffffffff) ^ state[0]) >> 19)
    state[1] = (((state[1] & 4294967288) << 4) & 0xffffffff) ^ \
               ((((state[1] << 2) & 0xffffffff) ^ state[1]) >> 25)
    state[2] = (((state[2] & 4294967280) << 17) & 0xffffffff) ^ \
               ((((state[2] << 3) & 0xffffffff) ^ state[2]) >> 11)

    return state[0] ^ state[1] ^ state[2]


@numba.njit('f4(i8[:])')
def tau_rand(state):
    """A fast (pseudo)-random number generator for floats in the range [0,1]

    Parameters
    ----------
    state: array of int64, shape (3,)
        The internal state of the rng

    Returns
    -------
    A (pseudo)-random float32 in the interval [0, 1]
    """
    integer = tau_rand_int(state)
    return float(integer) / 0x7fffffff


@numba.njit()
def norm(vec):
    """Compute the (standard l2) norm of a vector.

    Parameters
    ----------
    vec: array of shape (dim,)

    Returns
    -------
    The l2 norm of vec.
    """
    result = 0.0
    for i in range(vec.shape[0]):
        result += vec[i] ** 2
    return np.sqrt(result)


@numba.njit()
def rejection_sample(n_samples, pool_size, rng_state):
    """Generate n_samples many integers from 0 to pool_size such that no
    integer is selected twice. The duplication constraint is achieved via
    rejection sampling.

    Parameters
    ----------
    n_samples: int
        The number of random samples to select from the pool

    pool_size: int
        The size of the total pool of candidates to sample from

    rng_state: array of int64, shape (3,)
        Internal state of the random number generator

    Returns
    -------
    sample: array of shape(n_samples,)
        The ``n_samples`` randomly selected elements from the pool.
    """
    result = np.empty(n_samples, dtype=np.int64)
    for i in range(n_samples):
        reject_sample = True
        while reject_sample:
            j = tau_rand_int(rng_state) % pool_size
            for k in range(i):
                if j == result[k]:
                    break
            else:
                reject_sample = False
        result[i] = j
    return result


@numba.njit('f8[:, :, :](i8,i8)')
def make_heap(n_points, size):
    """Constructor for the numba enabled heap objects. The heaps are used
    for approximate nearest neighbor search, maintaining a list of potential
    neighbors sorted by their distance. We also flag if potential neighbors
    are newly added to the list or not. Internally this is stored as
    a single ndarray; the first axis determines whether we are looking at the
    array of candidate indices, the array of distances, or the flag array for
    whether elements are new or not. Each of these arrays are of shape
    (``n_points``, ``size``)

    Parameters
    ----------
    n_points: int
        The number of data points to track in the heap.

    size: int
        The number of items to keep on the heap for each data point.

    Returns
    -------
    heap: An ndarray suitable for passing to other numba enabled heap functions.
    """
    result = np.zeros((3, n_points, size))
    result[0] = -1
    result[1] = np.infty
    result[2] = 0

    return result


@numba.jit('i8(f8[:,:,:],i8,f8,i8,i8)')
def heap_push(heap, row, weight, index, flag):
    """Push a new element onto the heap. The heap stores potential neighbors
    for each data point. The ``row`` parameter determines which data point we
    are addressing, the ``weight`` determines the distance (for heap sorting),
    the ``index`` is the element to add, and the flag determines whether this
    is to be considered a new addition.

    Parameters
    ----------
    heap: ndarray generated by ``make_heap``
        The heap object to push into

    row: int
        Which actual heap within the heap object to push to

    weight: float
        The priority value of the element to push onto the heap

    index: int
        The actual value to be pushed

    flag: int
        Whether to flag the newly added element or not.

    Returns
    -------
    success: The number of new elements successfully pushed into the heap.
    """
    indices = heap[0, row]
    weights = heap[1, row]
    is_new = heap[2, row]

    if weight > weights[0]:
        return 0

    # break if we already have this element.
    for i in range(indices.shape[0]):
        if index == indices[i]:
            return 0

    # insert val at position zero
    weights[0] = weight
    indices[0] = index
    is_new[0] = flag

    # descend the heap, swapping values until the max heap criterion is met
    i = 0
    while True:
        ic1 = 2 * i + 1
        ic2 = ic1 + 1

        if ic1 >= heap.shape[2]:
            break
        elif ic2 >= heap.shape[2]:
            if weights[ic1] > weight:
                i_swap = ic1
            else:
                break
        elif weights[ic1] >= weights[ic2]:
            if weight < weights[ic1]:
                i_swap = ic1
            else:
                break
        else:
            if weight < weights[ic2]:
                i_swap = ic2
            else:
                break

        weights[i] = weights[i_swap]
        indices[i] = indices[i_swap]
        is_new[i] = is_new[i_swap]

        i = i_swap

    weights[i] = weight
    indices[i] = index
    is_new[i] = flag

    return 1


@numba.njit()
def deheap_sort(heap):
    """Given an array of heaps (of indices and weights), unpack the heap
    out to give and array of sorted lists of indices and weights by increasing
    weight. This is effectively just the second half of heap sort (the first
    half not being required since we already have the data in a heap).

    Parameters
    ----------
    heap : array of shape (3, n_samples, n_neighbors)
        The heap to turn into sorted lists.

    Returns
    -------
    indices, weights: arrays of shape (n_samples, n_neighbors)
        The indices and weights sorted by increasing weight.
    """
    indices = heap[0]
    weights = heap[1]

    for i in range(indices.shape[0]):
        heap_end = indices.shape[1] - 1
        while heap_end >= 0:
            indices[i, 0], indices[i, heap_end] = \
                indices[i, heap_end], indices[i, 0]
            weights[i, 0], weights[i, heap_end] = \
                weights[i, heap_end], weights[i, 0]
            heap_end -= 1

            root = 0
            while root * 2 + 1 < heap_end:
                left_child = root * 2 + 1
                right_child = left_child + 1
                swap = root

                if weights[i, swap] < weights[i, left_child]:
                    swap = left_child
                if right_child < heap_end and weights[i, swap] < weights[
                    i, right_child]:
                    swap = right_child

                if swap == root:
                    break
                else:
                    weights[i, root], weights[i, swap] = \
                        weights[i, swap], weights[i, root]
                    indices[i, root], indices[i, swap] = \
                        indices[i, swap], indices[i, root]

                    root = swap

    return indices.astype(np.int64), weights


@numba.njit('i8(f8[:, :, :],i8)')
def smallest_flagged(heap, row):
    ind = heap[0, row]
    dist = heap[1, row]
    flag = heap[2, row]

    min_dist = np.inf
    result_index = -1

    for i in range(ind.shape[0]):
        if flag[i] and dist[i] < min_dist:
            min_dist = dist[i]
            result_index = i

    if result_index >= 0:
        flag[result_index] = 0
        return int(ind[result_index])
    else:
        return -1

@numba.njit(parallel=True)
def build_candidates(current_graph, n_vertices, n_neighbors, max_candidates,
                     rng_state):
    """Build a heap of candidate neighbors for nearest neighbor descent. For
    each vertex the candidate neighbors are any current neighbors, and any
    vertices that have the vertex as one of their nearest neighbors.

    Parameters
    ----------
    current_graph: heap
        The current state of the graph for nearest neighbor descent.

    n_vertices: int
        The total number of vertices in the graph.

    n_neighbors: int
        The number of neighbor edges per node in the current graph.

    max_candidates: int
        The maximum number of new candidate neighbors.

    rng_state: array of int64, shape (3,)
        The internal state of the rng

    Returns
    -------
    candidate_neighbors: A heap with an array of (randomly sorted) candidate
    neighbors for each vertex in the graph.
    """
    candidate_neighbors = make_heap(n_vertices, max_candidates)
    for i in range(n_vertices):
        for j in range(n_neighbors):
            if current_graph[0, i, j] < 0:
                continue
            idx = current_graph[0, i, j]
            isn = current_graph[2, i, j]
            d = tau_rand(rng_state)
            heap_push(candidate_neighbors, i, d, idx, isn)
            heap_push(candidate_neighbors, idx, d, i, isn)
            current_graph[2, i, j] = 0

    return candidate_neighbors

