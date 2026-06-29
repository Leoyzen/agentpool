"""测试 agent pool 配置"""

import asyncio
from pydantic_ai.models.test import TestModel
from agentpool import AgentPool, NativeAgentConfig

async def test_agent_pool():
    """测试 AgentPool 配置管理"""
    
    # 创建简单的配置
    agent1_config = NativeAgentConfig(
        name="助手1",
        model=TestModel(custom_output_text="我是助手1，专门回答简单问题"),
        system_prompt="你是一个简单的问答助手"
    )
    
    agent2_config = NativeAgentConfig(
        name="助手2", 
        model=TestModel(custom_output_text="我是助手2，专门处理复杂问题"),
        system_prompt="你是一个复杂的问题处理专家"
    )
    
    # 创建 manifest
    from agentpool import AgentsManifest
    manifest = AgentsManifest(agents={
        "assistant1": agent1_config,
        "assistant2": agent2_config
    })
    
    print("✓ 配置创建成功")
    
    # 创建 agent pool
    async with AgentPool(manifest) as pool:
        print("✓ AgentPool 创建成功")
        
        # 获取并测试第一个 agent
        agent1 = pool.get_agent("assistant1")
        result1 = await agent1.run("你好")
        print(f"✓ 助手1 响应: {result1.content}")
        
        # 获取并测试第二个 agent
        agent2 = pool.get_agent("assistant2")
        result2 = await agent2.run("解决一个复杂问题")
        print(f"✓ 助手2 响应: {result2.content}")
        
        print("✓ 所有测试通过！")

if __name__ == "__main__":
    asyncio.run(test_agent_pool())