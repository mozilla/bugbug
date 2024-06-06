import pickle
import time
import os
import json
from scipy.sparse import hstack

def pickle_dump(data, pick_file):
    with open(pick_file, "wb") as f:
        pickle.dump(data, f)


def pickle_load(pick_file):
    with open(pick_file, "rb") as f:
        data = pickle.load(f)
    return data

def json_dump(data, pick_file):
    with open(pick_file, "w") as f:
        json.dump(data, f)


def json_load(pick_file):
    with open(pick_file, "r") as f:
        data = json.load(f)
    return data

a= 1

def run_and_pickle(fun, args, filename, recompute=False, do_print=True, save_compute_time=False):
    if save_compute_time:
        f = filename.split('.')
        assert len(f) == 2
        filename = '_timed.'.join(f)    
    
    start_time = time.time()
    if do_print:
        print('Load ', filename, end=' ... ')
    if not recompute and os.path.exists(filename):
        if save_compute_time:
            COMPUTED, compute_time = pickle_load(filename)
        else:
            COMPUTED = pickle_load(filename)
    else:
        if do_print:
            print('(computing)', end=' ...')
        COMPUTED = fun(**args)
        compute_time = round(time.time() - start_time, 2)
        if save_compute_time:
            pickle_dump((COMPUTED, compute_time), filename)
        else:
            pickle_dump(COMPUTED, filename)
    if do_print:
        print('Done in', round(time.time() - start_time, 2), 'sec')
    if save_compute_time:
        return COMPUTED, compute_time
    return COMPUTED


def run_and_json(fun, args, filename, recompute=False, do_print=True, save_compute_time=False):
    if save_compute_time:
        f = filename.split('.')
        assert len(f) == 2
        filename = '_timed.'.join(f)    
    
    start_time = time.time()
    print('Load ', filename, end=' ... ')
    if not recompute and os.path.exists(filename):
        if save_compute_time:
            COMPUTED, compute_time = json_load(filename)
        else:
            COMPUTED = json_load(filename)
    else:
        print('(computing)', end=' ...')
        COMPUTED = fun(**args)
        compute_time = round(time.time() - start_time, 2)
        if save_compute_time:
            json_dump((COMPUTED, compute_time), filename)
        else:
            json_dump(COMPUTED, filename)
    print('Done in', round(time.time() - start_time, 2), 'sec')
    if save_compute_time:
        return COMPUTED, compute_time
    return COMPUTED
