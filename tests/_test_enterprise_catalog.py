import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wb_proxy as proxy

MID = 'custom:gpt-6.1-sol|gpt-6.1-sol'
MODEL = {'id': MID, 'name': 'gpt-6.1-sol', 'tags': ['chat'],
         'supportsToolCall': True, 'maxInputTokens': 1000000}


def response(models, code=0):
    return io.BytesIO(json.dumps({'code': code, 'data': models}).encode())


def account(uid='cn-user', realm='cn', enterprise='enterprise/a', enabled=True):
    return SimpleNamespace(uid=uid, realm=realm, enterprise_id=enterprise,
                           enabled=enabled, access_token='test-only-token', proxy=None,
                           chat_base_url=lambda: 'https://example.invalid',
                           headers=lambda: {'Authorization': 'Bearer test-only-token',
                                            'X-Enterprise-Id': enterprise})


class EnterpriseCatalogTest(unittest.TestCase):
    def setUp(self):
        self.pool = SimpleNamespace(accounts=[account()])
        self.patches = [patch.object(proxy, 'POOL', self.pool),
                        patch.object(proxy, '_enterprise_models_cache', {}),
                        patch.object(proxy, '_models_cache', {}),
                        patch.object(proxy, '_catalog_meta', {}),
                        patch.object(proxy, '_catalog_refresh_at', {}),
                        patch.object(proxy, 'log'),
                        patch.object(proxy, 'note_remote_catalog'),
                        patch.object(proxy, 'note_bundled_reasoning')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_parse_chat_only_deduplicates_and_does_not_publish_secrets(self):
        models = [dict(MODEL, apiKey='must-not-leak', disabledMultiModel=True),
                  {'id': 'builtin', 'tags': ['chat']},
                  {'id': 'custom:completion|completion', 'tags': ['completion']}]
        entries = proxy.parse_enterprise_models({'code': 0, 'data': models})
        self.assertEqual([mid for mid, _ in entries], [MID])
        self.assertNotIn('apiKey', entries[0][1])
        self.assertTrue(entries[0][1]['disabledMultimodal'])
        self.assertEqual(entries[0][1]['catalog_type'], 'enterprise')
        self.assertEqual(len(proxy.parse_enterprise_models({'data': [MODEL, MODEL]})), 1)
        for bad in ({'code': 401, 'data': []}, {'data': {}}, {'data': [None]},
                    {'data': [dict(MODEL, tags='chat')]}):
            with self.assertRaises(ValueError):
                proxy.parse_enterprise_models(bad)

    def test_discovery_sends_enterprise_identity_and_escapes_id(self):
        self.pool.accounts += [account('disabled', enabled=False),
                               account('personal', enterprise=''), account('intl', realm='intl')]
        with patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])) as call:
            entries, state = proxy.fetch_enterprise_models('cn')
        self.assertEqual(state, {'source': 'remote', 'count': 1})
        self.assertEqual(entries[0][0], MID)
        call.assert_called_once()
        req = call.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/enterprise%2Fa/config/models'))
        self.assertEqual(req.get_header('X-enterprise-id'), 'enterprise/a')
        self.assertEqual(req.get_method(), 'GET')

    def test_cache_is_account_and_enterprise_scoped(self):
        with patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])):
            proxy.fetch_enterprise_models('cn')
        with patch.object(proxy.wb_accounts, 'urlopen', side_effect=TimeoutError):
            entries, state = proxy.fetch_enterprise_models('cn')
            self.assertEqual(state['source'], 'cache')
            self.assertEqual(len(entries), 1)
            self.pool.accounts = [account('other-user')]
            self.assertEqual(proxy.fetch_enterprise_models('cn')[0], [])
            self.pool.accounts = [account(enterprise='other-enterprise')]
            self.assertEqual(proxy.fetch_enterprise_models('cn')[0], [])

    def test_empty_or_auth_denial_removes_old_models(self):
        for failure in (None, 401, 403):
            with patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])):
                proxy.fetch_enterprise_models('cn')
            kw = ({'return_value': response([])} if failure is None else
                  {'side_effect': HTTPError('https://example.invalid', failure, '', {}, None)})
            with patch.object(proxy.wb_accounts, 'urlopen', **kw):
                self.assertEqual(proxy.fetch_enterprise_models('cn')[0], [])
            self.assertFalse(any(proxy._enterprise_models_cache.values()))

    def test_multiple_accounts_realms_and_duplicate_models(self):
        self.pool.accounts += [account('second'), account('intl', realm='intl')]
        with patch.object(proxy.wb_accounts, 'urlopen', side_effect=[response([MODEL]),
                TimeoutError(), response([dict(MODEL, id='custom:intl|intl')])]) as call:
            entries, state = proxy.fetch_enterprise_models('all')
        self.assertEqual(call.call_count, 3)
        self.assertEqual({mid for mid, _ in entries}, {MID, 'custom:intl|intl'})
        self.assertEqual(state['source'], 'partial')

    def test_automatic_discovery_includes_unpriced_enterprise_and_reuses_cache(self):
        with patch.object(proxy, 'curated_live_sources', return_value=([], False, None)), \
             patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])) as call:
            entries = proxy.fetch_models('cn')
            self.assertEqual(proxy.fetch_models('cn'), entries)
        call.assert_called_once()
        meta = dict(entries)[MID]
        self.assertNotIn('credits', meta)
        self.assertEqual(meta['catalog_type'], 'enterprise')
        self.assertTrue(all(m.get('catalog_type', 'builtin') == 'builtin' for mid, m in entries if mid != MID))
        self.assertEqual(proxy.catalog_meta('cn')['enterprise']['source'], 'remote')
        with patch.object(proxy.wb_modelsdev, 'lookup', return_value=(1000000, None, 'remote')), \
             patch.object(proxy.wb_probes, 'clamp_for', return_value=None):
            item = proxy.model_entry(MID, meta)
        self.assertEqual(item['catalog_type'], 'enterprise')
        self.assertEqual(item['id'], MID)
        self.assertTrue(item['supports_tool_calls'])

    def test_manual_refresh_works_when_product_catalog_fails(self):
        proxy._models_cache['cn'] = {'data': [('old-builtin', {'name': 'Keep'})]}
        with patch.object(proxy, 'fetch_remote_product_config', return_value=None), \
             patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])):
            result = proxy.refresh_catalog('cn')
        self.assertTrue(result['ok'])
        self.assertEqual(set(dict(proxy._models_cache['cn']['data'])), {'old-builtin', MID})
        self.assertEqual(proxy.catalog_meta('cn')['enterprise']['source'], 'remote')
        self.assertTrue(proxy.refresh_catalog('cn')['cooldown'])

    def test_manual_refresh_both_sources_and_removal_after_denial(self):
        with patch.object(proxy, 'fetch_remote_product_config', return_value=(['hy3'], {})), \
             patch.object(proxy.wb_accounts, 'urlopen', return_value=response([MODEL])):
            self.assertTrue(proxy.refresh_catalog('cn')['ok'])
        proxy._catalog_refresh_at.clear()
        with patch.object(proxy, 'fetch_remote_product_config', return_value=None), \
             patch.object(proxy.wb_accounts, 'urlopen', side_effect=HTTPError(
                 'https://example.invalid', 403, '', {}, None)):
            self.assertFalse(proxy.refresh_catalog('cn')['ok'])
        self.assertNotIn(MID, dict(proxy._models_cache['cn']['data']))
        self.assertEqual(proxy.catalog_meta('cn')['enterprise']['source'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
