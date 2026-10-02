"""Correlate trusted local journal receipts; absence is never a policy denial."""
import json
import re

from common import require

STAGES = ('common_target', 'provider', 'evidence_binding', 'remote_appraisal',
          'final_target', 'identity_delivery', 'ingress_ready')
TARGET_FIELDS = ('launch_id', 'container_id', 'pid', 'start_time', 'boot_id', 'policy_id')


def fields(message, event):
    tail = message.partition(event + ' ')[2]
    return dict(re.findall(r'(?:^|\s)([a-z_]+)=([^\s"\\]+)', tail)) if tail else None


def summarize(journal, target, started_at_ms, completed_at_ms, invocation, *, group='full_argus', ready=False, units=None):
    require(started_at_ms <= completed_at_ms, 'invalid admission attempt window')
    receipts, publications, subscriptions, malformed = [], [], [], 0
    boot = target.get('boot_id', '').replace('-', '')
    units = units or {'provider': 'argus-tdx-provider.service', 'agent': 'argus-workload-agent.service', 'helper': 'argus-helper.service'}
    for line in journal.splitlines():
        try:
            row = json.loads(line)
            at = int(row['__REALTIME_TIMESTAMP']) / 1000
        except (ValueError, TypeError, KeyError):
            malformed += 1
            continue
        if row.get('_BOOT_ID') != boot or not started_at_ms <= at <= completed_at_ms:
            continue
        message, unit = row.get('MESSAGE'), row.get('_SYSTEMD_UNIT')
        if not isinstance(message, str):
            continue
        if unit == units['helper'] and row.get('_SYSTEMD_INVOCATION_ID') == invocation:
            expected = {k: target.get(k) for k in ('launch_id', 'container_id', 'pid', 'start_time')}
            expected['policy'] = target.get('policy_id')
            for label, sink in (('workload subscription', subscriptions), ('target SVID published', publications)):
                value = fields(message, label)
                if value and all(value.get(k) == v for k, v in expected.items()) and value.get('subscription_id') == invocation:
                    sink.append({'at_ms': at, **value})
        if unit not in {units['provider'], units['agent']}:
            continue
        raw = message.partition('argus admission stage ')[2]
        if not raw:
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(raw)
            require(value.get('schema') == 'argus.admission-stage.v1', 'stage schema')
            require(value.get('component') == ('provider' if unit == units['provider'] else 'plugin'), 'stage source')
            require(re.fullmatch(r'[A-Za-z0-9_-]{43}', value.get('attempt_id', '')) is not None
                    and value.get('nonce') == value['attempt_id'], 'stage nonce')
            require(value.get('stage') in (*STAGES, 'evidence_collection'), 'stage name')
            require(value.get('status') in ('ALLOW', 'DENY', 'UNKNOWN'), 'stage status')
            require(re.fullmatch(r'[A-Z_]{1,80}', value.get('reason_code', '')) is not None, 'stage reason')
            require(all(value.get('target', {}).get(k) == target.get(k) for k in TARGET_FIELDS), 'stage target')
        except (ValueError, TypeError, AttributeError):
            malformed += 1
            continue
        receipts.append({**value, 'at_ms': at})
    receipts.sort(key=lambda item: item['at_ms'])
    # A target/time-window match alone is weaker than request binding. Require
    # this actual fresh Helper subscription; retain the association limitation.
    subscription_at = min((item['at_ms'] for item in subscriptions), default=None)
    receipts = [item for item in receipts if subscription_at is not None and item['at_ms'] >= subscription_at]
    nonces = list(dict.fromkeys(item['attempt_id'] for item in receipts))
    attempts = []
    for nonce in nonces:
        rows = [item for item in receipts if item['attempt_id'] == nonce]
        stages, stopped = [], False
        for stage in STAGES[:5]:
            found = [item for item in rows if item['stage'] == stage]
            if found:
                statuses = {item['status'] for item in found}
                value = found[-1] if len(statuses) == 1 else {'stage': stage, 'status': 'UNKNOWN', 'reason_code': 'CONFLICTING_RECEIPTS'}
            else:
                value = {'stage': stage, 'status': 'NOT_REACHED' if stopped else 'UNKNOWN', 'reason_code': 'NO_RECEIPT'}
            stages.append(value)
            stopped |= value['status'] == 'DENY'
        reject = next((item for item in stages if item['status'] == 'DENY'), None)
        # A later explicit denial does not establish the first layer if an
        # earlier layer's evidence is missing or conflicting.
        before = stages[:stages.index(reject)] if reject else stages
        first = reject['stage'] if reject and all(item['status'] == 'ALLOW' for item in before) else None
        attempts.append({'attempt_id': nonce, 'stages': stages, 'first_rejection_stage': first,
                         'explicit_rejection_stage': reject['stage'] if reject else None,
                         'classification': 'DENIED' if first else 'UNKNOWN' if reject else 'SELECTORS_RETURNED'
                         if all(item['status'] == 'ALLOW' for item in stages) else 'UNKNOWN'})
    identity = bool(publications and subscription_at is not None and any(item['at_ms'] >= subscription_at for item in publications))
    ingress = bool(ready and identity)
    matched = [item for item in attempts if item['classification'] == 'SELECTORS_RETURNED'
               and any(publication['at_ms'] >= max(row.get('at_ms', 0) for row in item['stages']) for publication in publications)]
    admitted = ingress and (group == 'native_spire_guarded' or bool(matched))
    rejected = next((item for item in attempts if item['first_rejection_stage']), None)
    return {'schema': 'argus.admission-stages.v1', 'attempts': attempts, 'stage_receipts': receipts,
            'first_rejection_stage': rejected['first_rejection_stage'] if rejected else None,
            'new_admission': 'ADMITTED' if admitted else 'DENIED' if rejected else 'UNKNOWN',
            'accepted_nonce': matched[-1]['attempt_id'] if matched and admitted else None,
            'identity_delivery': 'ALLOW' if identity else 'UNKNOWN', 'ingress_ready': 'ALLOW' if ingress else 'UNKNOWN',
            'subscriptions': subscriptions, 'publications': publications, 'malformed_records': malformed,
            'association': 'same guest boot, target, bounded time window and new Helper invocation; local operational evidence',
            'hardware_provenance': 'NOT_ESTABLISHED_BY_THIS_TOOL'}
