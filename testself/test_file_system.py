"""tool.file_system 文件与 CodeAgent 输入加载工具测试。"""
from __future__ import annotations

import json
from pathlib import Path

from agent.agents import CodeAgent
from tool.context import configure
from tool.file_system import load_code_inputs, read_file, write_file


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """开发 workspace 与测试 artifacts 分开，模拟 API/测试两种配置。"""
    workspace = tmp_path / "workspace"
    artifacts = tmp_path / "selfcache" / "artifacts"
    workspace.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    configure(workspace=workspace, artifacts=artifacts)
    return workspace, artifacts


def test_file_write_and_read(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)

    write_file.invoke({"path": "a.txt", "content": "hello"})

    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello"
    assert "hello" in read_file.invoke({"path": "a.txt"})


def test_load_code_inputs_uses_configured_artifacts(tmp_path: Path) -> None:
    """即使 workspace/artifacts 下也有同名文件，也必须读 ctx.artifacts。"""
    workspace, artifacts = _setup(tmp_path)
    module = "采购订单"

    # 开发产物：workspace/artifacts（不应被读取）
    dev_controls = workspace / "artifacts" / "controls"
    dev_controls.mkdir(parents=True)
    (dev_controls / f"{module}.json").write_text(
        '{"dev": true}', encoding="utf-8"
    )

    # 当前 context 的产物：selfcache/artifacts
    controls = artifacts / "controls"
    controls.mkdir()
    (controls / f"{module}.json").write_text(
        json.dumps({"controls": [{"name": "供应商"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    cases = artifacts / "cases"
    cases.mkdir()
    (cases / f"{module}_cases.json").write_text(
        json.dumps([{"id": "CGDD-001"}], ensure_ascii=False),
        encoding="utf-8",
    )

    result = json.loads(load_code_inputs.invoke({"module": module}))

    assert result["module"] == module
    assert result["controls"] == {"controls": [{"name": "供应商"}]}
    assert result["cases"] == [{"id": "CGDD-001"}]
    assert result["controls_source"].endswith(f"{module}.json")
    assert result["cases_source"].endswith(f"{module}_cases.json")


def test_load_code_inputs_falls_back_to_compact(tmp_path: Path) -> None:
    _, artifacts = _setup(tmp_path)
    module = "采购入库"
    dom = artifacts / "dom"
    dom.mkdir()
    (dom / f"{module}_facts_compact.json").write_text(
        '{"module": "采购入库", "list": {"buttons": ["新增"]}}',
        encoding="utf-8",
    )
    cases = artifacts / "cases"
    cases.mkdir()
    (cases / f"{module}_cases.json").write_text("[]", encoding="utf-8")

    result = json.loads(load_code_inputs.invoke({"module": module}))

    assert result["controls"] == {
        "module": "采购入库",
        "list": {"buttons": ["新增"]},
    }
    assert result["controls_source"].endswith(f"{module}_facts_compact.json")


def test_load_code_inputs_missing_module_lists_available_files(tmp_path: Path) -> None:
    _, artifacts = _setup(tmp_path)
    controls = artifacts / "controls"
    controls.mkdir()
    (controls / "采购订单.json").write_text("{}", encoding="utf-8")

    result = json.loads(load_code_inputs.invoke({"module": "不存在模块"}))

    assert "error" in result
    assert result["available"]["controls"] == ["采购订单.json"]


def test_load_code_inputs_rejects_path_traversal(tmp_path: Path) -> None:
    _setup(tmp_path)

    result = load_code_inputs.invoke({"module": "../.env"})

    assert result.startswith("错误：")
    assert "路径分隔符" in result


def test_read_file_blocks_env(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    secret = "sk-should-not-leak"
    (workspace / ".env").write_text(f"LLM_API_KEY={secret}\n", encoding="utf-8")

    result = read_file.invoke({"path": ".env"})

    assert "禁止访问敏感文件" in result
    assert secret not in result


def test_code_agent_persists_fenced_file(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    relative = "tests/generated/test_采购订单.py"
    code = "def test_cgdd_001():\n    assert True\n"
    content = f"```python file={relative}\n{code}```\n已生成。"

    agent = CodeAgent.__new__(CodeAgent)
    agent._last_output = None
    agent._postprocess_final(content, "生成采购订单代码")

    target = workspace / relative
    assert target.read_text(encoding="utf-8") == code
    assert agent._memory_content(content).startswith(
        "已生成测试代码：tests/generated/test_采购订单.py"
    )


def test_code_agent_rejects_path_outside_generated(tmp_path: Path) -> None:
    workspace, _ = _setup(tmp_path)
    content = "```python file=../evil.py\nprint('x')\n```"

    agent = CodeAgent.__new__(CodeAgent)
    agent._last_output = None
    result = agent._postprocess_final(content, "生成代码")

    assert "输出路径必须位于 tests/generated/" in result
    assert not (workspace.parent / "evil.py").exists()
