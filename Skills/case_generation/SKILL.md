# 测试用例生成技能（Case Generation）

## 1. 定位

读取控件采集 Agent 产出的控件 JSON 与业务语义信息，生成可执行、可断言的
ERP 测试用例，校验通过后写入 xlsx。只设计用例，不执行用例、不修改业务数据。

## 2. 输入

- 控件 JSON（固定命名：`artifacts/dom/<module>_capture.json`，精简版 `_facts_compact.json`）
- 业务语义：模块说明、字段含义、操作规则（可由控件采集 Agent 用 web 工具补充）
- 可选的模块名、历史用例、已有用例编号

## 3. 工具

- `list_dir`：路径不明确时先列出 `artifacts/dom`，确认实际文件名
- `read_file`：读取控件 JSON、已有用例和业务说明
- `write_file` / `edit_file`：保存用例 JSON、修订单条用例
- `web_search`：补充业务规则、字段含义和场景说明
- `validate_cases`：写 xlsx 前校验字段、编号、步骤、备注和执行情况
- `json_to_xlsx`：把校验通过的用例写成 xlsx（testcase 布局）
- `read_xlsx` / `xlsx_to_json`：读取已有 xlsx 用例，避免重复

## 4. 工作流

1. 先 `list_dir("artifacts/dom")` 找到 `<module>_capture.json`，再 `read_file` 读取，
   提取字段名、必填项、枚举值、按钮和表格列。
   若不确定当前 workspace 根目录，先从 `list_dir(".")` 逐级定位。
2. 按“正常流程 → 边界值 → 异常场景”设计用例，避免与已有用例重复。
3. 把测试点展开成连续编号的步骤，每步都有可断言的预期。
4. 输出用例 JSON（`id/priority/module/purpose/pre/steps/expected/assertion/manual/automation/note`）。
5. 调用 `validate_cases`；有错误先修 JSON，直到 `ok=true`。
6. 调用 `json_to_xlsx` 生成 xlsx，并回报文件路径。

## 5. 约束

- 字段名、按钮名、枚举值必须来自控件 JSON 或业务语义，不得凭空编造。
- 一条用例只验证一个主目的；步骤过多时拆分。
- 预期结果必须可断言（具体文本、字段值或状态），不写“正常显示”。
- `manual` / `automation` 初始为“待执行”，`note` 为“AI生成”。
- `steps` 用 `1. `、`2. ` 连续编号，`validate_cases` 会校验编号是否连续。
- 不执行用例、不调用浏览器操作。

## 6. 输出格式

用例 JSON 示例（字段名固定，`steps` 必须从 `1. ` 开始连续编号）：

    [
      {
        "id": "CGDD-001",
        "priority": "高",
        "module": "采购订单",
        "purpose": "新增采购订单成功",
        "pre": "已登录且有供应商数据",
        "steps": "1. 打开采购订单列表\n2. 点击新增\n3. 选择供应商并保存",
        "expected": "列表出现新单据，状态为待审核",
        "assertion": "列表首行单号与保存结果一致",
        "manual": "待执行",
        "automation": "待执行",
        "note": "AI生成"
      }
    ]

最终回报：

- 用例 JSON 路径
- 校验结果（`ok`、`count`、`errors`）
- xlsx 路径与行列数
- 引用说明：用到的控件字段和业务语义来源
