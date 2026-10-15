"""Local, synthetic botany fixtures. None are evidence for research conclusions."""

import json
from textwrap import dedent


DEMO_NOTICE = "合成演示数据，不作为真实科研结论"


def _jsonl(records):
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n"


def seed_documents():
    """Return editable documents and explicit quality-review fixtures."""
    chlorophyll_anomaly = "DEMO-CHL-003,拟南芥,叶绿素a,0.83,,合成演示数据，不作为真实科研结论"
    rice_anomaly = "DEMO-RICE-003 的净光合速率记录为 11.8；原始记录缺少单位，须回到实验记录核对。"
    return [
        {
            "name": "拟南芥干旱处理记录.md",
            "purpose": "both",
            "license": "self_owned",
            "is_demo": True,
            "content": dedent("""\
                # 拟南芥干旱处理记录（演示）

                > 合成演示数据，不作为真实科研结论。以下样本、时间和数值均为本应用生成的虚构记录，无对应实验、论文或真实研究者。

                ## 数据范围

                项目编号：DEMO-ARAB-2026。物种：拟南芥（Arabidopsis thaliana）。本资料用于演示原始记录保留、段落切分、来源追踪和训练正文导出。

                ## 原始观测记录

                样本 DEMO-ARAB-001 属于对照组，记录日为模拟处理后的第 0 天，盆土质量记录为 182.4 g。
                样本 DEMO-ARAB-002 属于演示干旱组，记录日为模拟处理后的第 3 天，盆土质量记录为 149.8 g。
                两条记录没有给出土壤干重、初始含水量或生物学重复数，不能据此计算相对含水量或作出处理效应结论。

                ## 加工约束

                正文语料应忠实保留以上原文、演示标记和单位。缺失信息保留为缺失；不得补造实验方法、统计显著性、引用文献或研究结论。
                可以在知识库回答“记录中有哪些样本及单位”，但回答应说明资料属于合成演示。
                """),
        },
        {
            "name": "水稻光合作用记录.md",
            "purpose": "both",
            "license": "self_owned",
            "is_demo": True,
            "content": dedent(f"""\
                # 水稻光合作用测量记录（演示）

                > {DEMO_NOTICE}。不存在对应真实实验，文中的日期、样本和数值仅用于功能测试。

                ## 记录字段

                物种：水稻（Oryza sativa）。叶片温度记录单位为 °C；净光合速率记录单位应随原始仪器输出保存。

                ## 原始记录

                DEMO-RICE-001：叶片温度 27.0 °C，净光合速率 12.4 μmol CO₂ m⁻² s⁻¹。

                DEMO-RICE-002：叶片温度 28.5 °C，净光合速率 10.9 μmol CO₂ m⁻² s⁻¹。

                {rice_anomaly}

                ## 待核对事项

                第三条记录进入单位复核队列。与前两条相邻不代表单位相同，系统不得自动填写单位。
                本资料没有重复测量、叶面积校准信息或统计设计，不支持推断温度与光合作用之间的因果关系。
                """),
            "review_flags": [
                {
                    "match": rice_anomaly,
                    "kind": "unit",
                    "message": "合成异常：DEMO-RICE-003 缺失净光合速率单位。请保留原始值，并查原始仪器记录；无法确认时保持待核对。",
                    "suggested_text": "DEMO-RICE-003 的净光合速率原始数值为 11.8，单位待确认；该记录不得用于跨样本比较。",
                }
            ],
        },
        {
            "name": "叶绿素测量.csv",
            "purpose": "both",
            "license": "self_owned",
            "is_demo": True,
            "content": (
                "sample_id,species,analyte,value,unit,data_notice\n"
                "DEMO-CHL-001,拟南芥,叶绿素a,0.91,mg/g鲜重,合成演示数据，不作为真实科研结论\n"
                "DEMO-CHL-002,拟南芥,叶绿素b,0.34,mg/g鲜重,合成演示数据，不作为真实科研结论\n"
                f"{chlorophyll_anomaly}\n"
                "DEMO-CHL-004,水稻,叶绿素a,1.02,mg/g鲜重,合成演示数据，不作为真实科研结论\n"
            ),
            "review_flags": [
                {
                    "match": chlorophyll_anomaly,
                    "kind": "unit",
                    "message": "合成异常：叶绿素记录 DEMO-CHL-003 的 unit 字段为空。0.83 不能直接当作 mg/g鲜重；需核对原始测量。",
                    "suggested_text": (
                        "字段：sample_id | species | analyte | value | unit | data_notice\n"
                        "DEMO-CHL-003,拟南芥,叶绿素a,0.83,待确认,合成演示数据，不作为真实科研结论\n"
                        "sample_id=DEMO-CHL-003; species=拟南芥; analyte=叶绿素a; value=0.83; unit=待确认; data_notice=合成演示数据，不作为真实科研结论"
                    ),
                }
            ],
        },
        {
            "name": "植物标本记录.jsonl",
            "purpose": "kb",
            "license": "research_only",
            "is_demo": True,
            "content": _jsonl([
                {
                    "specimen_id": "DEMO-SP-001",
                    "scientific_name": "Arabidopsis thaliana",
                    "common_name": "拟南芥",
                    "collection_site": "模拟温室 A 区（非真实采集地点）",
                    "collection_date": "2026-06-12",
                    "parts_recorded": ["叶", "茎"],
                    "image_file": None,
                    "data_notice": DEMO_NOTICE,
                    "rights_note": "演示许可标签：research_only；实际使用须另行核验来源授权。",
                },
                {
                    "specimen_id": "DEMO-SP-002",
                    "scientific_name": "Oryza sativa",
                    "common_name": "水稻",
                    "collection_site": "模拟温室 B 区（非真实采集地点）",
                    "collection_date": "2026-06-13",
                    "parts_recorded": ["叶"],
                    "image_file": None,
                    "data_notice": DEMO_NOTICE,
                    "rights_note": "记录中没有真实图片；系统不得生成标本图片作为原始证据。",
                },
            ]),
        },
        {
            "name": "种子萌发观察.csv",
            "purpose": "both",
            "license": "unknown",
            "is_demo": True,
            "content": (
                "sample_id,species,observation_day,day_unit,total_seeds,germinated_seeds,count_unit,temperature,temperature_unit,data_notice\n"
                "DEMO-GERM-001,拟南芥,2,d,20,7,粒,22,°C,合成演示数据，不作为真实科研结论\n"
                "DEMO-GERM-002,拟南芥,4,d,20,15,粒,22,°C,合成演示数据，不作为真实科研结论\n"
                "DEMO-GERM-003,水稻,3,d,20,12,粒,25,°C,合成演示数据，不作为真实科研结论\n"
            ),
        },
        {
            "name": "植物学指令样本_SFT.jsonl",
            "purpose": "training",
            "license": "self_owned",
            "is_demo": True,
            "content": _jsonl([
                {
                    "messages": [
                        {"role": "system", "content": "只依据给定记录作答。缺失信息应明确说明，不得补造科研事实。"},
                        {"role": "user", "content": "合成演示数据，不作为真实科研结论。原始记录：DEMO-ARAB-001 盆土质量为 182.4 g。请提取样本编号、数值和单位。"},
                        {"role": "assistant", "content": "样本编号：DEMO-ARAB-001；盆土质量：182.4；单位：g。该记录属于合成演示数据，不作为真实科研结论。"},
                    ],
                    "metadata": {"is_demo": True, "data_notice": DEMO_NOTICE, "source_id": "DEMO-ARAB-001", "task": "记录字段提取"},
                },
                {
                    "messages": [
                        {"role": "user", "content": "合成演示数据，不作为真实科研结论。原始记录：DEMO-RICE-003 净光合速率为 11.8，单位未记录。这个数值可以直接与 12.4 μmol CO₂ m⁻² s⁻¹ 比较吗？"},
                        {"role": "assistant", "content": "不能直接比较。DEMO-RICE-003 缺少单位，应先核对原始仪器记录；无法确认时保持待核对。两条数据都是合成演示数据，不支持真实科研结论。"},
                    ],
                    "metadata": {"is_demo": True, "data_notice": DEMO_NOTICE, "source_id": "DEMO-RICE-003", "task": "缺失信息识别"},
                },
            ]),
        },
        {
            "name": "植物学偏好样本.jsonl",
            "purpose": "training",
            "license": "self_owned",
            "is_demo": True,
            "content": _jsonl([
                {
                    "prompt": "合成演示数据，不作为真实科研结论。记录只有：DEMO-CHL-003 的叶绿素a数值为 0.83，单位为空。请解释如何处理。",
                    "chosen": "保留原始数值 0.83，将单位标为待确认，并进入人工复核。没有来源证据时不能自动补成 mg/g鲜重。此记录是合成演示数据，不支持真实科研结论。",
                    "rejected": "合成演示中的不合格回答：可以直接把单位填为 mg/g鲜重，并据此认定该植物叶绿素含量偏低。",
                    "metadata": {"is_demo": True, "data_notice": DEMO_NOTICE, "source_id": "DEMO-CHL-003", "preference_reason": "chosen 保留缺失信息；rejected 补造单位和无依据结论。"},
                }
            ]),
        },
    ]
