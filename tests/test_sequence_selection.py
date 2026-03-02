"""Tests for sequence mapping and persona-based sequence assignment."""
import json
import pytest


pytestmark = pytest.mark.unit


class TestSequenceMappingCRUD:
    """Test sequence_mappings table operations."""

    def test_upsert_creates_mapping(self, test_db):
        import database
        result = database.upsert_sequence_mapping(
            sequence_id='seq_100',
            sequence_name='Preparing - Technical',
            sequence_config='threaded_4',
            num_steps=4,
            active=True,
            owner_name='eric@phrase.com'
        )
        assert result['sequence_id'] == 'seq_100'
        assert result['sequence_name'] == 'Preparing - Technical'

    def test_upsert_updates_existing(self, test_db):
        import database
        database.upsert_sequence_mapping('seq_200', 'Original Name', num_steps=2)
        database.upsert_sequence_mapping('seq_200', 'Updated Name', num_steps=5)
        mappings = database.get_all_sequence_mappings()
        seq200 = [m for m in mappings if m['sequence_id'] == 'seq_200']
        assert len(seq200) == 1
        assert seq200[0]['sequence_name'] == 'Updated Name'
        assert seq200[0]['num_steps'] == 5

    def test_get_all_mappings(self, test_db):
        import database
        database.upsert_sequence_mapping('seq_a', 'Alpha Sequence')
        database.upsert_sequence_mapping('seq_b', 'Beta Sequence')
        database.upsert_sequence_mapping('seq_c', 'Charlie Sequence')
        mappings = database.get_all_sequence_mappings()
        assert len(mappings) >= 3
        names = [m['sequence_name'] for m in mappings]
        assert 'Alpha Sequence' in names

    def test_get_enabled_only(self, test_db):
        import database
        database.upsert_sequence_mapping('seq_on', 'Enabled Seq')
        database.upsert_sequence_mapping('seq_off', 'Disabled Seq')
        # Enable one
        mappings = database.get_all_sequence_mappings()
        on_id = [m for m in mappings if m['sequence_id'] == 'seq_on'][0]['id']
        database.toggle_sequence_mapping_enabled(on_id, True)
        enabled = database.get_all_sequence_mappings(enabled_only=True)
        assert all(m['enabled'] for m in enabled)
        enabled_ids = [m['sequence_id'] for m in enabled]
        assert 'seq_on' in enabled_ids

    def test_update_mapping(self, test_db):
        import database
        result = database.upsert_sequence_mapping('seq_upd', 'Update Me')
        mapping_id = result['id']
        ok = database.update_sequence_mapping(mapping_id, owner_name='new_owner@test.com')
        assert ok is True
        mappings = database.get_all_sequence_mappings()
        updated = [m for m in mappings if m['id'] == mapping_id][0]
        assert updated['owner_name'] == 'new_owner@test.com'

    def test_update_mapping_invalid_field(self, test_db):
        import database
        result = database.upsert_sequence_mapping('seq_inv', 'Invalid')
        ok = database.update_sequence_mapping(result['id'], hacker_field='evil')
        assert ok is False

    def test_delete_mapping(self, test_db):
        import database
        result = database.upsert_sequence_mapping('seq_del', 'Delete Me')
        ok = database.delete_sequence_mapping(result['id'])
        assert ok is True
        mappings = database.get_all_sequence_mappings()
        assert all(m['sequence_id'] != 'seq_del' for m in mappings)

    def test_delete_nonexistent(self, test_db):
        import database
        ok = database.delete_sequence_mapping(99999)
        assert ok is False

    def test_search_mappings(self, test_db):
        import database
        database.upsert_sequence_mapping('seq_prep', 'Preparing - Technical')
        database.upsert_sequence_mapping('seq_ghost', 'Ghost Branch - Urgent')
        database.upsert_sequence_mapping('seq_rfc', 'RFC Discussion')
        results = database.search_sequence_mappings('Preparing')
        assert len(results) >= 1
        assert results[0]['sequence_name'] == 'Preparing - Technical'

    def test_search_no_results(self, test_db):
        import database
        results = database.search_sequence_mappings('NonexistentSequence')
        assert results == []

    def test_toggle_enabled(self, test_db):
        import database
        result = database.upsert_sequence_mapping('seq_toggle', 'Toggle Test')
        mapping_id = result['id']
        database.toggle_sequence_mapping_enabled(mapping_id, True)
        mappings = database.get_all_sequence_mappings()
        m = [x for x in mappings if x['id'] == mapping_id][0]
        assert m['enabled'] == 1
        database.toggle_sequence_mapping_enabled(mapping_id, False)
        mappings = database.get_all_sequence_mappings()
        m = [x for x in mappings if x['id'] == mapping_id][0]
        assert m['enabled'] == 0


class TestCampaignPersonas:
    """Test persona-to-sequence assignment via campaign_personas."""

    def test_create_persona(self, test_db, sample_campaign):
        import database
        result = database.create_campaign_persona(
            campaign_id=sample_campaign,
            persona_name='Engineering',
            titles=['VP Engineering', 'Director of Engineering'],
            seniorities=['vp', 'director'],
            sequence_id='seq_001',
            sequence_name='Preparing - Technical',
            priority=0
        )
        assert result['persona_name'] == 'Engineering'

    def test_get_personas_ordered_by_priority(self, test_db, sample_campaign):
        import database
        database.create_campaign_persona(sample_campaign, 'Product', [], [], 'seq_p', priority=2)
        database.create_campaign_persona(sample_campaign, 'Engineering', [], [], 'seq_e', priority=0)
        database.create_campaign_persona(sample_campaign, 'Executive', [], [], 'seq_x', priority=1)
        personas = database.get_campaign_personas(sample_campaign)
        names = [p['persona_name'] for p in personas]
        assert names == ['Engineering', 'Executive', 'Product']

    def test_persona_titles_deserialized(self, test_db, sample_campaign):
        import database
        database.create_campaign_persona(
            sample_campaign, 'Eng',
            titles=['VP Engineering', 'CTO'],
            seniorities=['vp', 'c_suite'],
            sequence_id='seq_001'
        )
        personas = database.get_campaign_personas(sample_campaign)
        assert personas[0]['titles'] == ['VP Engineering', 'CTO']
        assert personas[0]['seniorities'] == ['vp', 'c_suite']

    def test_update_persona(self, test_db, sample_campaign):
        import database
        result = database.create_campaign_persona(sample_campaign, 'Eng', [], [], 'seq_old')
        ok = database.update_campaign_persona(result['id'], sequence_id='seq_new', priority=5)
        assert ok is True
        personas = database.get_campaign_personas(sample_campaign)
        p = [x for x in personas if x['id'] == result['id']][0]
        assert p['sequence_id'] == 'seq_new'
        assert p['priority'] == 5

    def test_delete_persona(self, test_db, sample_campaign):
        import database
        result = database.create_campaign_persona(sample_campaign, 'Delete Me', [], [], 'seq_x')
        ok = database.delete_campaign_persona(result['id'])
        assert ok is True
        personas = database.get_campaign_personas(sample_campaign)
        assert all(p['persona_name'] != 'Delete Me' for p in personas)

    def test_replace_all_personas(self, test_db, sample_campaign):
        import database
        database.create_campaign_persona(sample_campaign, 'Old1', [], [], 'seq_1')
        database.create_campaign_persona(sample_campaign, 'Old2', [], [], 'seq_2')
        count = database.replace_campaign_personas(sample_campaign, [
            {'persona_name': 'New1', 'titles': ['CTO'], 'seniorities': [], 'sequence_id': 'seq_n1'},
            {'persona_name': 'New2', 'titles': [], 'seniorities': ['manager'], 'sequence_id': 'seq_n2'},
            {'persona_name': 'New3', 'titles': [], 'seniorities': [], 'sequence_id': 'seq_n3'},
        ])
        assert count == 3
        personas = database.get_campaign_personas(sample_campaign)
        assert len(personas) == 3
        names = {p['persona_name'] for p in personas}
        assert names == {'New1', 'New2', 'New3'}

    def test_persona_unique_per_campaign(self, test_db, sample_campaign):
        """campaign_personas has unique constraint on (campaign_id, persona_name)."""
        import database
        import sqlite3 as sqlite
        database.create_campaign_persona(sample_campaign, 'Unique', [], [], 'seq_1')
        with pytest.raises(Exception):
            database.create_campaign_persona(sample_campaign, 'Unique', [], [], 'seq_2')


class TestSequenceMappingCampaignAssociation:
    """Test that sequence mappings properly link to campaigns."""

    def test_mapping_shows_associated_campaigns(self, test_db, sample_campaign):
        import database
        # Create a campaign with a sequence_id
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE campaigns SET sequence_id = ? WHERE id = ?",
            ('seq_linked', sample_campaign)
        )
        conn.commit()
        conn.close()

        database.upsert_sequence_mapping('seq_linked', 'Linked Sequence')
        mappings = database.get_all_sequence_mappings()
        linked = [m for m in mappings if m['sequence_id'] == 'seq_linked'][0]
        assert len(linked['campaigns']) == 1
        assert linked['campaigns'][0]['id'] == sample_campaign

    def test_get_campaigns_for_sequence(self, test_db, sample_campaign):
        import database
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE campaigns SET sequence_id = ? WHERE id = ?",
            ('seq_check', sample_campaign)
        )
        conn.commit()
        conn.close()

        campaigns = database.get_campaigns_for_sequence('seq_check')
        assert len(campaigns) == 1
        assert campaigns[0]['id'] == sample_campaign

    def test_no_campaigns_for_sequence(self, test_db):
        import database
        campaigns = database.get_campaigns_for_sequence('seq_orphan')
        assert campaigns == []
