# -*- coding: utf-8 -*-
"""pytest 公共配置：把仓库根目录加入 sys.path，使测试可直接 import qcc。

注意：仓库根目录下存在 ppci/ 源码目录（迁移源，未安装为包），
若不屏蔽会成为命名空间包干扰诊断；测试本身只 import qcc。
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
