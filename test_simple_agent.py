"""简单的 agentpool 测试脚本"""

import asyncio
from pydantic_ai.models.test import TestModel
from agentpool import Agent

async def test_basic_agent():
    """测试基本的 agent 功能"""
    # 创建一个简单的测试 agent
    model = TestModel(custom_output_text="你好！这是一个测试响应。")
    
    async with Agent(
        name="test-agent",
        model=model,
        system_prompt="你是一个有用的助手"
    ) as agent:
        print("✓ Agent 创建成功")
        
        # 测试简单运行
        result = await agent.run("你好！")
        print(f"✓ Agent 运行成功: {result.content}")
        
        # 测试流式运行
        print("\n测试流式输出:")
        async for event in agent.run_stream("介绍一下你自己"):
            if hasattr(event, 'delta') and event.delta:
                print(event.delta, end='', flush=True)
        print("\n✓ 流式运行成功")

if __name__ == "__main__":
    asyncio.run(test_basic_agent())