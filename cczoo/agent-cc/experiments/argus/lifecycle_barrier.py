#!/usr/bin/env python3
"""Arm/release an existing root-only lifecycle barrier; never replay Docker."""
import argparse
import importlib.util
import json
import os
from pathlib import Path

from common import require


def module():
    path = Path(__file__).resolve().parents[2] / 'core/tc_api/tc_api/experiment_barrier.py'
    spec = importlib.util.spec_from_file_location('argus_experiment_barrier', path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    arm = sub.add_parser('arm')
    arm.add_argument('--point', choices=sorted(module().POINTS), required=True)
    arm.add_argument('--operation-type', choices=('create', 'start', 'stop', 'rm'), required=True)
    arm.add_argument('--mutation-id')
    arm.add_argument('--timeout-seconds', type=int, default=300)
    for item in (arm, sub.add_parser('release'), sub.add_parser('status')):
        item.add_argument('--directory', required=True)
        item.add_argument('--barrier-id', required=True)
    args = p.parse_args()
    require(hasattr(os, 'geteuid') and os.geteuid() == 0, 'requires Linux root on isolated experiment host')
    api = module()
    directory = api.protected(args.directory, directory=True)
    require(directory.is_relative_to('/srv/argus-experiments'), 'directory must stay below /srv/argus-experiments')
    if args.action == 'arm':
        value = api.validate({'schema': 'argus.lifecycle-barrier.v1', 'barrier_id': args.barrier_id,
                              'point': args.point, 'operation_type': args.operation_type,
                              'mutation_id': args.mutation_id, 'timeout_seconds': args.timeout_seconds})
        api._exclusive(directory / 'armed.json', value)
        print(json.dumps({'armed': True, 'config': value, 'remote_result': 'NOT_RUN'}))
        return
    armed = api.validate(json.loads(api.protected(directory / 'armed.json').read_text()))
    require(armed['barrier_id'] == args.barrier_id, 'barrier ID mismatch')
    receipt = json.loads(api.protected(directory / (args.barrier_id + '.reached.json')).read_text())
    require(receipt['barrier_id'] == args.barrier_id and receipt['point'] == armed['point'], 'receipt mismatch')
    if args.action == 'release':
        require(receipt['point'] != 'after_rtmr_extend' or receipt['pid'] > 0, 'invalid measurement-cut receipt')
        api._exclusive(directory / (args.barrier_id + '.release.json'),
                       {'barrier_id': args.barrier_id, 'mutation_id': receipt['mutation_id'], 'action': 'continue'})
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
