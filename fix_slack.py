import sys
path = r'c:\Users\hp\pontis\backend\app\api\routes\slack.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the if calibration_stage == "real_sourcing_ready" line
if_idx = None
for i, l in enumerate(lines):
    if 'if calibration_stage == "real_sourcing_ready"' in l:
        if_idx = i
        break

sys.stdout.write('if_idx: ' + str(if_idx) + '\n')

for i in range(if_idx, if_idx + 12):
    sys.stdout.write(str(i+1) + ': ' + repr(lines[i]) + '\n')

# Determine what needs fixing
# _delivery_thread_ts should be at indent+4 from "if" statement
if_indent = len(lines[if_idx]) - len(lines[if_idx].lstrip())
p_inner = ' ' * (if_indent + 4)
p_args = ' ' * (if_indent + 8)

sys.stdout.write('if_indent=' + str(if_indent) + ' p_inner=' + repr(p_inner) + '\n')

# Find the _delivery_thread_ts line (should be right after the if)
dts_idx = if_idx + 1
while dts_idx < if_idx + 5 and '_delivery_thread_ts' not in lines[dts_idx]:
    dts_idx += 1

sys.stdout.write('dts_idx: ' + str(dts_idx) + ' line: ' + repr(lines[dts_idx]) + '\n')

# Find end of add_task block
task_end = dts_idx + 1
while task_end < dts_idx + 12:
    if lines[task_end].strip() == ')':
        break
    task_end += 1

sys.stdout.write('task_end: ' + str(task_end) + ' line: ' + repr(lines[task_end]) + '\n')

# Replace dts_idx..task_end+1 with properly indented block
new_block = [
    p_inner + '_delivery_thread_ts = str((message or {}).get("thread_ts") or message_ts or "").strip() or None\n',
    p_inner + 'background_tasks.add_task(\n',
    p_args  + '_run_calibration_candidate_delivery_event,\n',
    p_args  + 'channel_id=channel_id,\n',
    p_args  + 'job_id=job_id,\n',
    p_args  + 'recruiter_id=internal_user_id or user_id,\n',
    p_args  + 'thread_ts=_delivery_thread_ts,\n',
    p_args  + 'bot_token=bot_token,\n',
    p_inner + ')\n',
]

lines[dts_idx:task_end + 1] = new_block

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

sys.stdout.write('saved total=' + str(len(lines)) + '\n')
