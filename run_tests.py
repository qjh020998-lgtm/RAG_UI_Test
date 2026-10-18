"""Test runner script."""

import sys
import pytest


def main():
    """运行测试套件。"""
    args = [
        "testcases/",
        "-v",
        "--tb=short",
        "-s",
    ]

    # 支持命令行传入额外参数
    if len(sys.argv) > 1:
        args.extend(sys.argv[1:])

    exit_code = pytest.main(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
