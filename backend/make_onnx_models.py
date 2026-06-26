import os
import numpy as np
import onnx
from onnx import helper, TensorProto

BASE = os.path.dirname(__file__)
MODEL_DIR = os.path.join(BASE, "models")
os.makedirs(MODEL_DIR, exist_ok=True)

def build_model(classes, filename):
    input_tensor = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1,3,224,224])
    output_tensor = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1,classes])

    flat_dim = 3*224*224
    W = np.random.randn(flat_dim, classes).astype("float32") * 0.01
    B = np.random.randn(classes).astype("float32") * 0.01

    flatten = helper.make_node("Flatten", ["input"], ["flat"])
    W_init = helper.make_tensor("W", TensorProto.FLOAT, W.shape, W.flatten())
    B_init = helper.make_tensor("B", TensorProto.FLOAT, B.shape, B.flatten())
    gemm = helper.make_node("Gemm", ["flat","W","B"], ["output"], alpha=1.0, beta=1.0)

    graph = helper.make_graph(
        [flatten, gemm],
        "SkinModel",
        [input_tensor],
        [output_tensor],
        [W_init, B_init]
    )
    model = helper.make_model(graph)
    onnx.checker.check_model(model)
    onnx.save(model, os.path.join(MODEL_DIR, filename))
    print(f"Saved {filename}")

print("Generating ONNX models…")
build_model(5, "skin_type_model.onnx")    # 5 skin types
build_model(6, "skin_issue_model.onnx")   # 6 issues
print("Done.")
