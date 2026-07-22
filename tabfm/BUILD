load("@rules_python//python:defs.bzl", "py_library")

package(default_visibility = ["//visibility:public"])

exports_files(["LICENSE"])

py_library(
    name = "tabfm",
    srcs = ["__init__.py"],
    deps = [
        "//tabfm/src:classifier_and_regressor",
        "//tabfm/src:tabfm_v1_0_0",
    ],
)
