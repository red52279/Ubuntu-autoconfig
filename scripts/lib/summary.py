#!/usr/bin/env python3
"""Persist per-run results and render an installation summary on every exit."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shlex

LABELS = dict(installed='新安装', configured='修复配置', reinstalled='卸载后重装', existing='已存在，跳过',
              checked='重新检查', maintenance='维护完成', disabled='已禁用', failed='失败',
              interrupted='中断', planned='预演', cancelled='取消', background='交给后台', not_started='未开始')


def append_event(path, feature, status, reason='', code=0, seconds=0, label=None):
    event = dict(feature=feature,label=label or feature,status=status,reason=reason,
                 exit_code=code,duration_seconds=seconds,time=datetime.now().astimezone().isoformat(timespec='seconds'))
    with Path(path).open('a') as stream:
        stream.write(json.dumps(event,ensure_ascii=False) + '\n')
        stream.flush()


def render(events, exit_code, log, dry_run=False, run=None, config=None, input_method='auto', selected=(), elapsed=None):
    events=list(events)
    if not any(event['status']=='background' for event in events):
        recorded={event['feature'] for event in events}
        for name in selected:
            if name not in recorded:
                events.append(dict(feature=name,label=name,status='not_started',reason='前置步骤失败、中断或取消，未开始执行',exit_code=exit_code,duration_seconds=0))
    counts = Counter(event['status'] for event in events)
    failed = list(dict.fromkeys(e['feature'] for e in events if e['status'] in ('failed','interrupted')))
    from catalog import FEATURES
    retry = None
    if failed:
        retry='sudo ./install.sh --cli'
        if config: retry+=' --config '+shlex.quote(str(config))
        retry+=' --input-method '+shlex.quote(input_method)
        if all(name in FEATURES for name in failed): retry+=' --only '+shlex.quote(','.join(failed))
    packages = {}
    if run and (Path(run)/'manifest.json').exists():
        receipt = json.loads((Path(run)/'manifest.json').read_text())
        before, after = receipt.get('packages_before',{}),receipt.get('packages_after',{})
        packages = dict(added=sorted(set(after)-set(before)), removed=sorted(set(before)-set(after)),
                        upgraded=sorted(name for name in before.keys() & after.keys() if before[name] != after[name]))
    data = dict(finished_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                exit_code=exit_code,dry_run=dry_run,counts=dict(counts),steps=events,
                packages=packages,log=str(log),rollback_record=str(run) if run else None,retry_command=retry,
                config=str(config) if config else None,input_method=input_method,elapsed_seconds=elapsed)
    outcome = ('已中断' if exit_code in (130,143) else '有失败项目' if exit_code else
               '已取消' if counts.get('cancelled') else '已交给后台执行' if counts.get('background') else '本次处理完成')
    lines = ['安装结果总结' + ('（预演，未执行安装）' if dry_run else ''), '结果: ' + outcome]
    if elapsed is not None: lines.append('运行耗时: ' + str(elapsed) + ' 秒')
    if counts: lines.append('；'.join(f'{LABELS.get(key,key)} {value}' for key,value in counts.items()))
    for event in events:
        suffix = ('：' + event['reason']) if event['reason'] else ''
        lines.append(f"  [{LABELS.get(event['status'],event['status'])}] {event['label']}{suffix}")
    if packages:
        lines.append(f"本安装记录累计包变化: 新增 {len(packages['added'])}，移除 {len(packages['removed'])}，版本变化 {len(packages['upgraded'])}")
    lines.append('完整日志: ' + str(log))
    if run: lines.append('回滚记录: ' + str(run))
    if retry: lines.append('重试命令: ' + retry)
    return data,'\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['event','finish'])
    parser.add_argument('--events',required=True,type=Path)
    parser.add_argument('--feature')
    parser.add_argument('--status')
    parser.add_argument('--reason',default='')
    parser.add_argument('--label')
    parser.add_argument('--code',type=int,default=0)
    parser.add_argument('--seconds',type=int,default=0)
    parser.add_argument('--log')
    parser.add_argument('--run')
    parser.add_argument('--config')
    parser.add_argument('--input-method',default='auto')
    parser.add_argument('--selected',default='')
    parser.add_argument('--elapsed',type=int)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if args.action=='event':
        append_event(args.events,args.feature,args.status,args.reason,args.code,args.seconds,args.label)
        return
    events=[json.loads(line) for line in args.events.read_text().splitlines() if line.strip()] if args.events.exists() else []
    data,text=render(events,args.code,args.log,args.dry_run,args.run,args.config,args.input_method,
                     [name for name in args.selected.split(',') if name],args.elapsed)
    base=Path(args.log).with_suffix('.summary')
    base.with_suffix('.summary.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    base.with_suffix('.summary.txt').write_text(text)
    print(text,end='')
    print('结构化总结: ' + str(base.with_suffix('.summary.json')))


if __name__=='__main__': main()
