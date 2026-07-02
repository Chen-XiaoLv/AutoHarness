# Task Contract
## Objective

系统需要根据当前输入中的描述性线索，识别被描述或指向的目标对象，并输出其名称或标识。

## Input Semantics

输入通常包含对某个对象的定义、特征、关系、用途、事件背景或其他线索。这些线索可能直接或间接指向一个目标对象，也可能包含噪声或冗余信息。
例如：
- 输入："In 1993 this country split into Slovakia & the Czech Republic"
- 输出："Czechoslovakia"


## Input–Output Relation

输出应是输入描述所指向的目标对象名称或标识。该对象可以是人物、地点、组织、物品、作品、药物、物种、事件、概念或其他可命名对象。

## Evidence Policy

优先依据输入中提供的描述线索和上下文信息进行判断。必要时可以结合常识性实体关系进行匹配，但不应输出与输入线索无关的答案。

## Output Contract

最终输出应为简短、明确的目标对象名称或标识，不包含推理过程、解释说明、完整句子、多个候选答案或无关内容。
