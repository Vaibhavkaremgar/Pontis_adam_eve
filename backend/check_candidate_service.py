lines = open('app/services/candidate_service.py', encoding='utf-8', errors='replace').readlines()
hits = [1847, 1903, 1961, 1962, 2129, 2130, 3550, 3551, 3581, 3582, 4854, 4855, 5010, 5011, 5062]
for h in hits:
    start = max(0, h-6)
    end = min(len(lines), h+3)
    print(f'--- Line {h} context ---')
    for i in range(start, end):
        marker = '>>>' if i+1 == h else '   '
        print(f'{marker} {i+1}: {lines[i].rstrip()}')
    print()
