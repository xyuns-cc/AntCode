"""
心跳模块

提供心跳上报和Worker能力检测功能。

本包不做子模块聚合导入。聚合会经 reporter 把整个 transport 栈拖进来，
连 metric_models 这类纯数据模块也要付 693 个模块的代价；
子模块请直接从各自叶子模块导入。

Requirements: 10.1, 10.2, 10.3, 10.4
"""
