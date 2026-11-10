"""Offline demo: run with `python demo.py`. No extra dependencies."""
import json
from simulator import WorkflowSimulator

if __name__ == '__main__':
    sim = WorkflowSimulator()
    result = sim.prepare_wechat_message('测试联系人', '今晚八点到')
    # Never print an approval token to stdout in a production integration.
    for step in result['steps']:
        step['result'].pop('approval_id', None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
