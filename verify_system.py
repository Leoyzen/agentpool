"""AgentPool 系统验证脚本"""

import asyncio
from pydantic_ai.models.test import TestModel
from agentpool import Agent

async def verify_system():
    """验证 AgentPool 系统核心功能"""
    
    print("🔍 AgentPool 系统功能验证")
    print("=" * 50)
    
    # 1. 验证基本 Agent 创建和执行
    print("\n📋 验证 1: 基本 Agent 功能")
    try:
        model = TestModel(custom_output_text="✅ 基本功能正常")
        async with Agent(name="verification-agent", model=model) as agent:
            result = await agent.run("系统验证")
            assert "✅" in result.content
            print(f"   结果: {result.content}")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False
    
    # 2. 验证流式输出
    print("\n📋 验证 2: 流式输出支持")
    try:
        model = TestModel(custom_output_text="流式内容验证")
        async with Agent(name="stream-verify", model=model) as agent:
            content_parts = []
            async for event in agent.run_stream("流式验证"):
                if hasattr(event, 'delta') and event.delta:
                    content_parts.append(str(event.delta))
            
            full_content = ''.join(content_parts)
            assert "流式内容验证" in full_content
            print(f"   结果: 收集到 {len(content_parts)} 个内容块")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False
    
    # 3. 验证异步并发
    print("\n📋 验证 3: 异步并发支持")
    try:
        async def run_agent_task(name):
            model = TestModel(custom_output_text=f"{name} 完成")
            async with Agent(name=name, model=model) as agent:
                result = await agent.run(f"{name} 任务")
                return result
        
        results = await asyncio.gather(
            run_agent_task("任务A"),
            run_agent_task("任务B"), 
            run_agent_task("任务C"),
            return_exceptions=True
        )
        
        if any(isinstance(r, Exception) for r in results):
            raise Exception("并发任务执行失败")
        
        print(f"   结果: 成功执行 {len(results)} 个并发任务")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False
    
    # 4. 验证事件处理
    print("\n📋 验证 4: 事件系统")
    try:
        from agentpool.agents.events import PartStartEvent, StreamCompleteEvent
        
        model = TestModel(custom_output_text="事件验证")
        async with Agent(name="event-verify", model=model) as agent:
            event_types = []
            
            async for event in agent.run_stream("事件测试"):
                event_types.append(type(event).__name__)
            
            # 验证至少有部分开始和流完成事件
            has_start = any('PartStartEvent' in t for t in event_types)
            has_complete = any('StreamCompleteEvent' in t for t in event_types)
            
            if not (has_start and has_complete):
                raise Exception(f"缺少必要事件: {event_types}")
            
            print(f"   结果: 捕获到 {len(event_types)} 个事件")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False
    
    # 5. 验证配置管理
    print("\n📋 验证 5: 配置管理")
    try:
        from agentpool import AgentPool, NativeAgentConfig, AgentsManifest
        
        agent_config = NativeAgentConfig(
            name="config-test",
            model="test",
            system_prompt="配置验证"
        )
        
        manifest = AgentsManifest(agents={"test": agent_config})
        
        async with AgentPool(manifest) as pool:
            assert "test" in pool.keys()
            assert len(list(pool.keys())) == 1
            print(f"   结果: 成功加载 {len(list(pool.keys()))} 个配置")
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False
    
    # 最终结果
    print("\n" + "=" * 50)
    print("✅ 所有核心功能验证通过！")
    print("=" * 50)
    
    print("""
    📊 系统功能状态：
    
    ✅ 基础 Agent 架构
    ✅ 流式事件输出  
    ✅ 异步并发处理
    ✅ 事件驱动机制
    ✅ 配置管理系统
    
    🚀 AgentPool 系统运行正常，可以投入使用！
    """)
    
    return True

if __name__ == "__main__":
    success = asyncio.run(verify_system())
    exit(0 if success else 1)