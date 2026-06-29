"""测试 agent pool 配置 - 简化版"""

import asyncio
from pydantic_ai.models.test import TestModel
from agentpool import AgentPool, NativeAgentConfig

async def test_agent_pool():
    """测试 AgentPool 配置管理"""
    
    # 创建简单的配置，不使用复杂模型
    agent1_config = NativeAgentConfig(
        name="助手1",
        model="test",
        system_prompt="你是一个简单的问答助手"
    )
    
    agent2_config = NativeAgentConfig(
        name="助手2", 
        model="test",
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
        print(f"✓ 可用的 agents: {list(pool.keys())}")
        
        # 测试注册的 agents 数量
        assert len(pool.keys()) == 2, f"Expected 2 agents, got {len(pool.keys())}"
        print("✓ Agent 数量验证正确")
        
        print("✓ 所有测试通过！")

if __name__ == "__main__":
    asyncio.run(test_agent_pool())