import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi.testclient import TestClient

from Labs import gateway


PREFIX = '/api/v1/labs/'
SECRET = 'gateway-test-secret-not-used-in-deployment'
DEFAULTS = {
    'lab1/attendance-report': dict(term='Базы данных', period_start='2026-09-01',
                                   period_end='2026-09-30', min_attendance_pct=0.0,
                                   max_attendance_pct=100.0, limit=10),
    'lab2/audience-report': dict(semester=2, enrollment_year=2023),
    'lab3/hours-report': dict(period_start='2026-09-01',
                             period_end='2026-09-30'),
}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(gateway.app)
        self.addCleanup(self.client.close)
        self.secret = patch.object(gateway, 'JWT_SECRET', SECRET)
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.upstream = AsyncMock()
        self.upstream.get.return_value = httpx.Response(200, json={'ok': True})
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=self.upstream)
        manager.__aexit__ = AsyncMock(return_value=False)
        self.http = patch.object(gateway.httpx, 'AsyncClient', return_value=manager)
        self.http.start()
        self.addCleanup(self.http.stop)
        self.token = jwt.encode({'sub': 'test', 'exp': datetime.now(timezone.utc) + timedelta(minutes=5)},
                                SECRET, algorithm='HS256')
        self.headers = {'Authorization': 'Bearer ' + self.token}

    def test_all_proxies_forward_defaults_without_query_parameters(self):
        for path, expected in DEFAULTS.items():
            with self.subTest(path=path):
                self.upstream.get.reset_mock()
                response = self.client.get(PREFIX + path, headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {'ok': True})
                self.upstream.get.assert_awaited_once()
                call = self.upstream.get.call_args
                self.assertEqual(call.kwargs['params'], expected)
                self.assertTrue(call.args[0].endswith(PREFIX + path))
                self.assertEqual(call.kwargs['headers'], self.headers)

    def test_explicit_values_override_defaults_and_keep_other_parameters(self):
        overrides = {
            'lab1/attendance-report': dict(term='индексы', period_end='2026-09-08', limit=3),
            'lab2/audience-report': dict(semester=4, enrollment_year=2024),
            'lab3/hours-report': dict(period_start='2026-09-03'),
        }
        for path, values in overrides.items():
            with self.subTest(path=path):
                response = self.client.get(PREFIX + path, params={**values, 'extra': 'value'},
                                           headers=self.headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.upstream.get.call_args.kwargs['params'],
                                 {**DEFAULTS[path], **values, **({'extra': 'value'} if not path.startswith('lab3/') else {})})

    def test_lab3_does_not_forward_group_filter(self):
        response = self.client.get(PREFIX + 'lab3/hours-report',
                                   params={'group_id': 'old-group'}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.upstream.get.call_args.kwargs['params'], DEFAULTS['lab3/hours-report'])

    def test_openapi_defaults_are_optional(self):
        paths = self.client.get('/openapi.json').json()['paths']
        for path, expected in DEFAULTS.items():
            with self.subTest(path=path):
                params = paths[PREFIX + path]['get']['parameters']
                self.assertEqual({p['name']: p['schema'].get('default') for p in params}, expected)
                self.assertTrue(all(not p['required'] for p in params))

    def test_all_proxies_require_valid_unexpired_token(self):
        expired = jwt.encode({'sub': 'test', 'exp': 1}, SECRET, algorithm='HS256')
        for path in DEFAULTS:
            for headers in ({}, {'Authorization': 'Bearer invalid'},
                            {'Authorization': 'Bearer ' + expired}):
                with self.subTest(path=path, headers_present=bool(headers)):
                    response = self.client.get(PREFIX + path, headers=headers)
                    self.assertEqual(response.status_code, 401, response.text)
        self.upstream.get.assert_not_awaited()

    def test_bad_numeric_parameters_are_rejected_before_proxying(self):
        for path, values in [('lab1/attendance-report', {'limit': 'abc'}),
                             ('lab2/audience-report', {'semester': 'abc'})]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(PREFIX + path, params=values,
                                                  headers=self.headers).status_code, 422)
        self.upstream.get.assert_not_awaited()

    def test_upstream_failures_and_timeouts_are_not_success(self):
        for path in DEFAULTS:
            with self.subTest(path=path):
                self.upstream.get.side_effect = None
                self.upstream.get.return_value = httpx.Response(404, json={'detail': 'Не найдено'})
                self.assertEqual(self.client.get(PREFIX + path, headers=self.headers).status_code, 404)
                self.upstream.get.side_effect = httpx.ReadTimeout('Нет ответа')
                self.assertEqual(self.client.get(PREFIX + path, headers=self.headers).status_code, 503)


if __name__ == '__main__':
    unittest.main()
