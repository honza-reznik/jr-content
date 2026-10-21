"""Souhrn CPU/RAM podle fáze z stats-runN.tsv: python3 souhrn-mereni.py vysledky/stats-run2.tsv"""
import csv,sys,re,collections
def mib(s):
    v=s.split('/')[0].strip(); m=re.match(r'([\d.]+)\s*([KMG]i?B)',v); n=float(m[1]); u=m[2]
    return n*{'KiB':1/1024,'MiB':1,'GiB':1024,'kB':1/1024,'MB':1,'GB':1024}[u]
rows=list(csv.DictReader(open(sys.argv[1]),delimiter='\t'))
g=collections.defaultdict(list)
for r in rows:
    ph=r['phase'].split('-')[-1] if '-' in r['phase'] else r['phase']
    g[(ph,r['container'].replace('jrc-audit-freeframe-',''))].append((float(r['cpu'].rstrip('%')),mib(r['ram'])))
for k in sorted(g):
    c=[x[0] for x in g[k]]; m=[x[1] for x in g[k]]
    print(f"{k[0]:11} {k[1]:12} n={len(c):3} cpu avg {sum(c)/len(c):6.1f}% max {max(c):6.1f}%  ram avg {sum(m)/len(m):7.1f} max {max(m):7.1f} MiB")
# total per sample
t=collections.defaultdict(lambda:[0,0,''])
for r in rows:
    t[r['time']][0]+=float(r['cpu'].rstrip('%')); t[r['time']][1]+=mib(r['ram']); t[r['time']][2]=r['phase']
ph=collections.defaultdict(list)
for k,v in t.items(): ph[v[2].split('-')[-1]].append(v)
for p,v in ph.items(): print('TOTAL',p,len(v),'cpu avg %.0f max %.0f ram avg %.0f max %.0f'%(sum(x[0] for x in v)/len(v),max(x[0] for x in v),sum(x[1] for x in v)/len(v),max(x[1] for x in v)))
