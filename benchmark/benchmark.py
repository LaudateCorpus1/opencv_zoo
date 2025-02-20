import os
import argparse

import yaml
import tqdm
import numpy as np
import cv2 as cv

from models import MODELS

parser = argparse.ArgumentParser("Benchmarks for OpenCV Zoo.")
parser.add_argument('--cfg', '-c', type=str,
                    help='Benchmarking on the given config.')
args = parser.parse_args()

class Timer:
    def __init__(self, warmup=0, reduction='median'):
        self._warmup = warmup
        self._reduction = reduction
        self._tm = cv.TickMeter()
        self._time_record = []
        self._calls = 0

    def start(self):
        self._tm.start()

    def stop(self):
        self._tm.stop()
        self._calls += 1
        self._time_record.append(self._tm.getTimeMilli())
        self._tm.reset()

    def reset(self):
        self._time_record = []
        self._calls = 0

    def getResult(self):
        if self._reduction == 'median':
            return self._getMedian(self._time_record[self._warmup:])
        elif self._reduction == 'gmean':
            return self._getGMean(self._time_record[self._warmup:])
        else:
            raise NotImplementedError()

    def _getMedian(self, records):
        ''' Return median time
        '''
        l = len(records)
        mid = int(l / 2)
        if l % 2 == 0:
            return (records[mid] + records[mid - 1]) / 2
        else:
            return records[mid]

    def _getGMean(self, records, drop_largest=3):
        ''' Return geometric mean of time
        '''
        time_record_sorted = sorted(records, reverse=True)
        return sum(records[drop_largest:]) / (self._calls - drop_largest)

class Data:
    def __init__(self, **kwargs):
        self._path = kwargs.pop('path', None)
        assert self._path, 'Benchmark[\'data\'][\'path\'] cannot be empty.'

        self._files = kwargs.pop('files', None)
        if not self._files:
            print('Benchmark[\'data\'][\'files\'] is empty, loading all images by default.')
            self._files = list()
            for filename in os.listdir(self._path):
                if filename.endswith('jpg') or filename.endswith('png'):
                    self._files.append(filename)

        self._use_label = kwargs.pop('useLabel', False)
        if self._use_label:
            self._labels = self._load_label()

    def _load_label(self):
        labels = dict.fromkeys(self._files, None)
        for filename in self._files:
            labels[filename] = np.loadtxt(os.path.join(self._path, '{}.txt'.format(filename[:-4])))
        return labels

    def __getitem__(self, idx):
        image = cv.imread(os.path.join(self._path, self._files[idx]))
        if self._use_label:
            return self._files[idx], image, self._labels[self._files[idx]]
        else:
            return self._files[idx], image

class Metric:
    def __init__(self, **kwargs):
        self._sizes = kwargs.pop('sizes', None)
        self._warmup = kwargs.pop('warmup', 3)
        self._repeat = kwargs.pop('repeat', 10)
        assert self._warmup < self._repeat, 'The value of warmup must be smaller than the value of repeat.'
        self._batch_size = kwargs.pop('batchSize', 1)
        self._reduction = kwargs.pop('reduction', 'median')

        self._timer = Timer(self._warmup, self._reduction)

    def getReduction(self):
        return self._reduction

    def forward(self, model, *args, **kwargs):
        img = args[0]
        h, w, _ = img.shape
        if not self._sizes:
            self._sizes = [[w, h]]

        results = dict()
        self._timer.reset()
        if len(args) == 1:
            for size in self._sizes:
                img_r = cv.resize(img, size)
                model.setInputSize(size)
                # TODO: batched inference
                # input_data = [img] * self._batch_size
                input_data = img_r
                for _ in range(self._repeat+self._warmup):
                    self._timer.start()
                    model.infer(input_data)
                    self._timer.stop()
                results[str(size)] = self._timer.getResult()
        else:
            # TODO: batched inference
            # input_data = [args] * self._batch_size
            bboxes = args[1]
            for idx, bbox in enumerate(bboxes):
                for _ in range(self._repeat+self._warmup):
                    self._timer.start()
                    model.infer(img, bbox)
                    self._timer.stop()
                results['bbox{}'.format(idx)] = self._timer.getResult()

        return results

class Benchmark:
    def __init__(self, **kwargs):
        self._data_dict = kwargs.pop('data', None)
        assert self._data_dict, 'Benchmark[\'data\'] cannot be empty and must have path and files.'
        self._data = Data(**self._data_dict)

        self._metric_dict = kwargs.pop('metric', None)
        self._metric = Metric(**self._metric_dict)

        backend_id = kwargs.pop('backend', 'default')
        available_backends = dict(
            default=cv.dnn.DNN_BACKEND_DEFAULT,
            # halide=cv.dnn.DNN_BACKEND_HALIDE,
            # inference_engine=cv.dnn.DNN_BACKEND_INFERENCE_ENGINE,
            opencv=cv.dnn.DNN_BACKEND_OPENCV,
            # vkcom=cv.dnn.DNN_BACKEND_VKCOM,
            cuda=cv.dnn.DNN_BACKEND_CUDA
        )
        self._backend = available_backends[backend_id]

        target_id = kwargs.pop('target', 'cpu')
        available_targets = dict(
            cpu=cv.dnn.DNN_TARGET_CPU,
            # opencl=cv.dnn.DNN_TARGET_OPENCL,
            # opencl_fp16=cv.dnn.DNN_TARGET_OPENCL_FP16,
            # myriad=cv.dnn.DNN_TARGET_MYRIAD,
            # vulkan=cv.dnn.DNN_TARGET_VULKAN,
            # fpga=cv.dnn.DNN_TARGET_FPGA,
            cuda=cv.dnn.DNN_TARGET_CUDA,
            cuda_fp16=cv.dnn.DNN_TARGET_CUDA_FP16,
            # hddl=cv.dnn.DNN_TARGET_HDDL
        )
        self._target = available_targets[target_id]

        self._benchmark_results = dict()

    def run(self, model):
        model.setBackend(self._backend)
        model.setTarget(self._target)

        for data in self._data:
            self._benchmark_results[data[0]] = self._metric.forward(model, *data[1:])

    def printResults(self):
        for imgName, results in self._benchmark_results.items():
            print('  image: {}'.format(imgName))
            total_latency = 0
            for key, latency in results.items():
                total_latency += latency
                print('      {}, latency ({}): {:.4f} ms'.format(key, self._metric.getReduction(), latency))


def build_from_cfg(cfg, registery):
    obj_name = cfg.pop('name')
    obj = registery.get(obj_name)
    return obj(**cfg)

def prepend_pythonpath(cfg, key1, key2):
    pythonpath = os.environ['PYTHONPATH']
    if cfg[key1][key2].startswith('/'):
        return
    cfg[key1][key2] = os.path.join(pythonpath, cfg[key1][key2])

if __name__ == '__main__':
    assert args.cfg.endswith('yaml'), 'Currently support configs of yaml format only.'
    with open(args.cfg, 'r') as f:
        cfg = yaml.safe_load(f)

    # prepend PYTHONPATH to each path
    prepend_pythonpath(cfg['Benchmark'], key1='data', key2='path')
    prepend_pythonpath(cfg, key1='Model', key2='modelPath')

    # Instantiate benchmarking
    benchmark = Benchmark(**cfg['Benchmark'])

    # Instantiate model
    model = build_from_cfg(cfg=cfg['Model'], registery=MODELS)

    # Run benchmarking
    print('Benchmarking {}:'.format(model.name))
    benchmark.run(model)
    benchmark.printResults()