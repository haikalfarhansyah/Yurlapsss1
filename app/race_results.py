"""Shared statistics. Grid launch segments are excluded from pace statistics."""
import math
import statistics


def result_rows(drivers, cfg):
    rows = []
    for i, source in enumerate(drivers):
        d = dict(source)
        laps = d.get('laps') or []
        full = laps[1:] if cfg.get('start_mode', 'grid') == 'grid' else laps
        ordered = sorted(full)
        mean = statistics.mean(full) if full else None
        sigma = statistics.pstdev(full) if full else None
        speeds = [p['speed_kmh'] for p in d.get('passings', []) if p.get('speed_kmh') is not None]
        d.update(no=d.get('no', i+1), n_lap=len(laps), total_ms=sum(laps) if laps else None,
                 best_ms=min(full) if full else None, rata_ms=mean, sigma_ms=sigma,
                 terakhir_ms=laps[-1] if laps else None,
                 top3_ms=statistics.mean(ordered[:3]) if len(full)>=3 else None,
                 consecutive_ms=min((sum(full[j:j+3])/3 for j in range(len(full)-2)),default=None),
                 p80_ms=statistics.mean(ordered[:math.ceil(len(full)*.8)]) if full else None,
                 consistency=round(max(0,1-sigma/mean)*100,1) if mean else None,
                 top_speed_kmh=max(speeds) if speeds else None,
                 average_speed_kmh=round(statistics.mean(speeds),2) if speeds else None, kmh=None)
        field={'best':'best_ms','top3':'top3_ms','consecutive':'consecutive_ms'}.get(cfg.get('rank'))
        score=[d[field] if d[field] is not None else float('inf')] if field else [-len(laps), d['total_ms'] if laps else float('inf')]
        d['_score']=score+[d.get('last_decoder_ts') or float('inf'),d['no']]
        rows.append(d)
    rows.sort(key=lambda d:d['_score'])
    for i,d in enumerate(rows):
        d.pop('_score');d['pos']=i+1;d['change']=d['no']-d['pos']
        d['gap_leader']=gap(d,rows[0]);d['gap_previous']=gap(d,rows[i-1]) if i else None
    return rows


def gap(driver,ahead):
    if driver is ahead or not driver['n_lap'] or not ahead['n_lap']:return None
    if driver['n_lap']!=ahead['n_lap']:return {'laps':ahead['n_lap']-driver['n_lap']}
    return {'ms':driver['total_ms']-ahead['total_ms']}


def chart_data(drivers):
    n=max((len(d.get('laps',[])) for d in drivers),default=0)
    cumul=[[sum(d['laps'][:j+1]) for j in range(len(d.get('laps',[])))] for d in drivers]
    positions=[[] for _ in drivers];gaps=[[] for _ in drivers]
    for lap in range(n):
        racers=sorted((times[lap],i) for i,times in enumerate(cumul) if len(times)>lap)
        for pos,(total,i) in enumerate(racers,1):
            positions[i].append([lap+1,pos]);gaps[i].append([lap+1,(total-racers[0][0])/1000])
    return {'positions':positions,'gaps':gaps}
