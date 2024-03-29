import io
import numpy as np
import os, json
from typing import List
from PIL import Image
import cv2

from triton_python_backend_utils import get_output_config_by_name, triton_string_to_numpy, get_input_config_by_name, get_input_tensor_by_name
from c_python_backend_utils import Tensor, InferenceResponse, InferenceRequest


def normalizeMeanVariance(in_img, mean=(0.485, 0.456, 0.406), variance=(0.229, 0.224, 0.225)):
    # should be RGB order
    img = in_img.copy().astype(np.float32)

    img -= np.array([mean[0] * 255.0, mean[1] * 255.0, mean[2] * 255.0], dtype=np.float32)
    img /= np.array([variance[0] * 255.0, variance[1] * 255.0, variance[2] * 255.0], dtype=np.float32)
    return img


class TritonPythonModel(object):
    def __init__(self):
        self.output_names = {
            'output': 'output',
            'image': 'image',
            'scale': 'scale'
        }

    def initialize(self, args):
        model_config = json.loads(args['model_config'])

        output_configs = {k: get_output_config_by_name(
            model_config, name) for k, name in self.output_names.items()}
        for k, cfg in output_configs.items():
            if cfg is None:
                raise ValueError(
                    f'Output {self.output_names[k]} is not defined in the model config')
            if 'dims' not in cfg:
                raise ValueError(
                    f'Dims for output {self.output_names[k]} are not defined in the model config')
            if 'name' not in cfg:
                raise ValueError(
                    f'Name for output {self.output_names[k]} is not defined in the model config')
            if 'data_type' not in cfg:
                raise ValueError(
                    f'Data type for output {self.output_names[k]} is not defined in the model config')

        self.output_dtypes = {k: triton_string_to_numpy(
            cfg['data_type']) for k, cfg in output_configs.items()}

    def execute(self, inference_requests: List[InferenceRequest]) -> List[InferenceResponse]:
        input_name = 'input'
        output_name = 'output'

        responses = []
        for request in inference_requests:
            # This model only process one input per request. We use
            # get_input_tensor_by_name instead of checking
            # len(request.inputs()) to allow for multiple inputs but
            # only process the one we want. Same rationale for the outputs
            batch_in_tensor: Tensor = get_input_tensor_by_name(request, input_name)
            if batch_in_tensor is None:
                raise ValueError(f'Input tensor {input_name} not found '
                                 f'in request {request.request_id()}')

            if output_name not in request.requested_output_names():
                raise ValueError(f'The output with name {output_name} is '
                                 f'not in the requested outputs '
                                 f'{request.requested_output_names()}')

            batch_in = batch_in_tensor.as_numpy()  # shape (batch_size, 1)

            if batch_in.dtype.type is not np.object_:
                raise ValueError(f'Input datatype must be np.object_, '
                                 f'got {batch_in.dtype.type}')

            batch_out = {k: [] for k, name in self.output_names.items(
            ) if name in request.requested_output_names()}

            newsize = (2240, 1920) # model text detection shape
            for img in batch_in:  # img is shape (1,)
                pil_img = Image.open(io.BytesIO(img[0]))
                h, w = np.array(pil_img).shape[:2]
                batch_out['scale'].append([w/2240, h/1920])
                resized_image = np.array(pil_img.resize(newsize))
                if len(resized_image.shape) == 3:
                    image = np.transpose(normalizeMeanVariance(resized_image.copy()), (2, 0, 1))
                elif len(np.array(pil_img).shape) == 2:
                    resized_image = cv2.cvtColor(resized_image, cv2.COLOR_GRAY2BGR)
                    image = np.transpose(normalizeMeanVariance(resized_image.copy()), (2, 0, 1))
                else:
                    raise ValueError(f"Do not support input image with {len(np.array(pil_img).shape)} channels")
                batch_out['image'].append(resized_image)
                batch_out['output'].append(np.array(image, dtype=np.float32))
            
            # Format outputs to build an InferenceResponse
            # Assumes there is only one output
            output_tensors = [Tensor(self.output_names[k], np.asarray(
                out, dtype=self.output_dtypes[k])) for k, out in batch_out.items()]

            # TODO: should set error field from InferenceResponse constructor
            # to handle errors
            response = InferenceResponse(output_tensors)
            responses.append(response)

        return responses
