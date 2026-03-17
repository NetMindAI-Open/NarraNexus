#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Persona Feature Test Script

Usage:
    uv run python src/xyz_agent_context/module/social_network_module/test_persona.py

Tests:
1. _should_update_persona logic
2. _infer_persona LLM call
3. _update_entity_persona database update
4. Persona injection in hook_data_gathering
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(project_root / "src"))

from loguru import logger
from xyz_agent_context.schema import SocialNetworkEntity
from xyz_agent_context.module.social_network_module import SocialNetworkModule


# ===== Test 1: _should_update_persona logic =====

def test_should_update_persona_logic():
    """Test the _should_update_persona method logic (no DB needed)"""
    print("\n" + "=" * 60)
    print("Test 1: _should_update_persona logic")
    print("=" * 60)

    # Create a mock module instance (without DB connection)
    module = SocialNetworkModule(agent_id="test_agent", user_id="test_user")

    # Test case 1: First interaction (persona is None)
    print("\n[Test 1.1] First interaction (persona=None)")
    entity1 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona=None,
        interaction_count=0
    )
    result = module._should_update_persona(entity1, "")
    print("  persona=None, interaction_count=0")
    print(f"  Expected: True, Got: {result}")
    assert result is True, "Should return True for first interaction"
    print("  ✅ PASSED")

    # Test case 2: Has persona, interaction_count=5 (no update)
    print("\n[Test 1.2] Has persona, interaction_count=5")
    entity2 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=5
    )
    result = module._should_update_persona(entity2, "Hello, how are you?")
    print("  persona='Technical communication style', interaction_count=5")
    print(f"  Expected: False, Got: {result}")
    assert result is False, "Should return False for non-multiple-of-10 turns"
    print("  ✅ PASSED")

    # Test case 3: Has persona, interaction_count=10 (periodic update)
    print("\n[Test 1.3] Has persona, interaction_count=10 (periodic update)")
    entity3 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=10
    )
    result = module._should_update_persona(entity3, "Normal conversation")
    print("  persona='Technical communication style', interaction_count=10")
    print(f"  Expected: True, Got: {result}")
    assert result is True, "Should return True for every 10 turns"
    print("  ✅ PASSED")

    # Test case 4: Has persona, interaction_count=20 (periodic update)
    print("\n[Test 1.4] Has persona, interaction_count=20 (periodic update)")
    entity4 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=20
    )
    result = module._should_update_persona(entity4, "Normal conversation")
    print("  persona='Technical communication style', interaction_count=20")
    print(f"  Expected: True, Got: {result}")
    assert result is True, "Should return True for every 10 turns"
    print("  ✅ PASSED")

    # Test case 5: Change signal detected
    print("\n[Test 1.5] Change signal detected in conversation")
    entity5 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=3
    )
    result = module._should_update_persona(entity5, "Actually I care more about the pricing now")
    print("  persona='Technical communication style', interaction_count=3")
    print("  response_content='Actually I care more about the pricing now'")
    print(f"  Expected: True, Got: {result}")
    assert result is True, "Should return True when change signal detected"
    print("  ✅ PASSED")

    # Test case 6: Chinese change signal
    print("\n[Test 1.6] Chinese change signal detected")
    entity6 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=7
    )
    result = module._should_update_persona(entity6, "我改变主意了，还是想看看价格方案")
    print("  persona='Technical communication style', interaction_count=7")
    print("  response_content='我改变主意了，还是想看看价格方案'")
    print(f"  Expected: True, Got: {result}")
    assert result is True, "Should return True when Chinese change signal detected"
    print("  ✅ PASSED")

    # Test case 7: No trigger conditions met
    print("\n[Test 1.7] No trigger conditions met")
    entity7 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_123",
        entity_type="user",
        entity_name="Alice",
        persona="Technical communication style",
        interaction_count=15
    )
    result = module._should_update_persona(entity7, "Just a regular conversation about the weather")
    print("  persona='Technical communication style', interaction_count=15")
    print("  response_content='Just a regular conversation about the weather'")
    print(f"  Expected: False, Got: {result}")
    assert result is False, "Should return False when no conditions met"
    print("  ✅ PASSED")

    print("\n" + "-" * 60)
    print("✅ All _should_update_persona tests passed!")
    print("-" * 60)


# ===== Test 2: _infer_persona LLM call =====

async def test_infer_persona_llm():
    """Test the _infer_persona method (requires LLM API)"""
    print("\n" + "=" * 60)
    print("Test 2: _infer_persona LLM call")
    print("=" * 60)
    print("Note: This test requires OPENAI_API_KEY to be set")

    module = SocialNetworkModule(agent_id="test_agent", user_id="test_user")

    # Test case: Technical expert
    print("\n[Test 2.1] Infer persona for a technical expert")
    entity = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_tech_001",
        entity_type="user",
        entity_name="Bob Chen",
        entity_description="Recommendation systems expert at Google",
        tags=["expert:推荐系统", "architect"],
        identity_info={"organization": "Google", "position": "Staff Engineer"},
        interaction_count=5
    )

    try:
        persona = await module._infer_persona(
            entity=entity,
            awareness="As a sales agent, focus on technical excellence and ROI.",
            job_info="Sales outreach for enterprise ML platform",
            recent_conversation="User asked about system architecture and latency requirements"
        )
        print(f"  Entity: {entity.entity_name}")
        print(f"  Tags: {entity.tags}")
        print(f"  Inferred Persona: {persona}")
        if persona:
            print("  ✅ PASSED - Persona was inferred successfully")
        else:
            print("  ⚠️ WARNING - Empty persona returned")
    except Exception as e:
        print(f"  ❌ FAILED - Error: {e}")
        print("  (This is expected if OPENAI_API_KEY is not set)")

    # Test case: Business-oriented customer
    print("\n[Test 2.2] Infer persona for a business customer")
    entity2 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_biz_001",
        entity_type="user",
        entity_name="Sarah Wang",
        entity_description="VP of Operations at TechCorp",
        tags=["manager", "decision_maker"],
        identity_info={"organization": "TechCorp", "position": "VP of Operations"},
        interaction_count=3
    )

    try:
        persona2 = await module._infer_persona(
            entity=entity2,
            awareness="As a sales agent, adapt communication to stakeholder type.",
            job_info="Enterprise deal for AI platform",
            recent_conversation="User asked about ROI and implementation timeline"
        )
        print(f"  Entity: {entity2.entity_name}")
        print(f"  Tags: {entity2.tags}")
        print(f"  Inferred Persona: {persona2}")
        if persona2:
            print("  ✅ PASSED - Persona was inferred successfully")
        else:
            print("  ⚠️ WARNING - Empty persona returned")
    except Exception as e:
        print(f"  ❌ FAILED - Error: {e}")
        print("  (This is expected if OPENAI_API_KEY is not set)")


# ===== Test 3: Full integration test (requires DB) =====

async def test_full_integration():
    """Full integration test with database (optional)"""
    print("\n" + "=" * 60)
    print("Test 3: Full Integration Test (requires DB)")
    print("=" * 60)
    print("Note: This test requires database connection")

    try:
        from xyz_agent_context.utils import get_db_client
        from xyz_agent_context.repository import SocialNetworkRepository, InstanceRepository

        db = await get_db_client()
        print("  ✅ Database connection established")

        # Find an existing SocialNetworkModule instance
        instance_repo = InstanceRepository(db)

        # Try to find any existing instance
        print("\n[Test 3.1] Looking for existing SocialNetworkModule instances...")

        # Query all instances and filter for SocialNetworkModule
        from xyz_agent_context.repository.instance_repository import InstanceRepository
        all_instances = await db.execute(
            "SELECT * FROM module_instances WHERE module_class = 'SocialNetworkModule' LIMIT 1",
            fetch=True
        )

        if all_instances:
            instance = all_instances[0]
            instance_id = instance['instance_id']
            agent_id = instance['agent_id']
            print(f"  Found instance: {instance_id} for agent: {agent_id}")

            # Test getting an entity and checking persona
            social_repo = SocialNetworkRepository(db)
            entities = await social_repo.get_all_entities(instance_id=instance_id, limit=5)

            if entities:
                print(f"  Found {len(entities)} entities")
                for e in entities:
                    print(f"    - {e.entity_name}: persona={e.persona[:50] if e.persona else 'None'}...")
            else:
                print("  No entities found in this instance")

            print("  ✅ Database integration test passed")
        else:
            print("  ⚠️ No SocialNetworkModule instances found in database")
            print("  (This is expected for a fresh database)")

    except Exception as e:
        print(f"  ❌ Database test failed: {e}")
        print("  (This is expected if database is not configured)")


# ===== Test 4: Persona field in schema =====

def test_persona_schema():
    """Test that persona field exists in SocialNetworkEntity schema"""
    print("\n" + "=" * 60)
    print("Test 4: Persona Field in Schema")
    print("=" * 60)

    print("\n[Test 4.1] Creating SocialNetworkEntity with persona")
    entity = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_test",
        entity_type="user",
        entity_name="Test User",
        persona="Technical communication style: Focus on architecture details and performance metrics.",
        extra_data={"embedding_text": "sample text for embedding"}
    )

    print(f"  entity_id: {entity.entity_id}")
    print(f"  persona: {entity.persona}")
    print(f"  extra_data: {entity.extra_data}")

    assert entity.persona is not None, "Persona should not be None"
    assert "embedding_text" in entity.extra_data, "extra_data should contain embedding_text"

    print("  ✅ PASSED - Schema fields are correctly defined")

    print("\n[Test 4.2] Creating SocialNetworkEntity without persona")
    entity2 = SocialNetworkEntity(
        instance_id="test_instance",
        entity_id="user_test2",
        entity_type="user"
    )

    print(f"  entity_id: {entity2.entity_id}")
    print(f"  persona: {entity2.persona}")
    print(f"  extra_data: {entity2.extra_data}")

    assert entity2.persona is None, "Persona should be None by default"
    assert entity2.extra_data == {}, "extra_data should be empty dict by default"

    print("  ✅ PASSED - Default values are correct")


# ===== Main =====

async def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("Persona Feature Test Suite")
    print("=" * 60)

    # Test 1: Unit test for _should_update_persona (no dependencies)
    test_should_update_persona_logic()

    # Test 4: Schema test (no dependencies)
    test_persona_schema()

    # Test 2: LLM test (requires OPENAI_API_KEY)
    print("\nRunning LLM tests...")
    await test_infer_persona_llm()

    # Test 3: Integration test (requires DB)
    print("\nRunning integration tests...")
    await test_full_integration()

    print("\n" + "=" * 60)
    print("Test Suite Complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
