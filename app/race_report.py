"""Offline PDF report using the same saved results as the live interface."""
from io import BytesIO
from datetime import datetime
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Circle, String
from .race_results import chart_data

PALETTE=['#e65a43','#4988db','#19a56a','#a769dc','#da8e20','#24a9b4','#c8508b','#7a9b2b','#65758d']


def duration(value):
    if value is None:return '-'
    sec=value/1000
    return f'{int(sec//60)}:{sec%60:06.3f}' if sec>=60 else f'{sec:.3f}'


def gap(value):
    if not value:return '-'
    return f"{value['laps']:+d}L" if 'laps' in value else f"{value['ms']/1000:+.3f}"


def table(data,widths,font=7):
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([
        ('BACKGROUND',(0,0),(-1,0),colors.HexColor('#f2f4f7')),
        ('TEXTCOLOR',(0,0),(-1,0),colors.HexColor('#536174')),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
        ('FONTNAME',(0,1),(-1,-1),'Helvetica'),
        ('FONTSIZE',(0,0),(-1,-1),font),('VALIGN',(0,0),(-1,-1),'MIDDLE'),
        ('LEADING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#fafafa')]),
        ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#dce0e5'))]))
    return t


def line_chart(drivers,kind,cfg=None):
    data=chart_data(drivers,cfg)[kind];drawing=Drawing(780,360)
    n=max((len(d.get('laps',[])) for d in drivers),default=1)
    high=max(1,max((v for series in data for _,v in series),default=1))
    def x(v):return 42+(v-1)/max(1,n-1)*710
    def y(v):return 325-(v-1)/max(1,high-1)*270 if kind=='positions' else 55+v/high*270
    for j in range(6):
        v=1+(high-1)*j/5 if kind=='positions' else high*j/5
        drawing.add(Line(42,y(v),752,y(v),strokeColor=colors.HexColor('#dce3ec'),strokeWidth=.5))
        drawing.add(String(4,y(v)-3,('P'+str(round(v))) if kind=='positions' else f'{v:.1f}',fontSize=8))
    for j in range(1,n+1):
        if j in (1,n) or j%max(1,(n+11)//12)==0:drawing.add(String(x(j)-3,39,str(j),fontSize=8))
    for i,series in enumerate(data):
        color=colors.HexColor(PALETTE[i%len(PALETTE)])
        pts=[c for lap,v in series for c in (x(lap),y(v))]
        if len(pts)>=4:drawing.add(PolyLine(pts,strokeColor=color,strokeWidth=1.3))
        for lap,v in series:drawing.add(Circle(x(lap),y(v),2,fillColor=color,strokeColor=color))
    drawing.add(String(380,18,'Lap',fontSize=9))
    return drawing


def distribution(drivers,cfg):
    # RaceEngine stores only measured laps: the first decoder crossing is kept
    # as a baseline and is never appended to ``laps``.  Do not discard another
    # value here; doing so made the PDF disagree with the in-app leaderboard.
    pace=[sorted(d.get('laps') or []) for d in drivers]
    values=[v for laps in pace for v in laps];height=max(120,len(drivers)*27+50)
    drawing=Drawing(780,height)
    if not values:
        drawing.add(String(10,height-30,'No full laps available',fontSize=10));return drawing
    low,high=min(values),max(values)
    def x(v):return 160+(v-low)/max(1,high-low)*580
    for i,(d,laps) in enumerate(zip(drivers,pace)):
        y=height-30-i*27
        drawing.add(String(0,y-3,(d.get('nick') or d['name'])[:26],fontSize=8))
        if not laps:continue
        n=len(laps);q1=laps[int((n-1)*.25)];q3=laps[int((n-1)*.75)];median=(laps[(n-1)//2]+laps[n//2])/2
        drawing.add(Line(x(laps[0]),y,x(laps[-1]),y,strokeColor=colors.HexColor('#edaaa3'),strokeWidth=9))
        drawing.add(Line(x(q1),y,x(q3),y,strokeColor=colors.HexColor('#da594d'),strokeWidth=9))
        drawing.add(Line(x(median),y-7,x(median),y+7,strokeColor=colors.HexColor('#172c43'),strokeWidth=1.5))
    for j in range(6):
        v=low+(high-low)*j/5;drawing.add(String(x(v)-8,10,f'{v/1000:.2f}s',fontSize=8))
    return drawing


def render_pdf(race):
    stream=BytesIO();width,height=landscape(A4);usable=width-56
    doc=SimpleDocTemplate(stream,pagesize=(width,height),leftMargin=28,rightMargin=28,topMargin=68,bottomMargin=32)
    styles=getSampleStyleSheet();styles['Normal'].fontSize=8;styles['Normal'].leading=10
    drivers=race.get('drivers',[]);cfg=race.get('cfg',{});story=[]
    def para(text,style='Normal'):return Paragraph(escape(str(text)),styles[style])
    def heading(title):
        title_flow = para(title, 'Heading3')
        title_flow.keepWithNext = True
        spacer = Spacer(1, 5)
        spacer.keepWithNext = True
        story.extend([title_flow, spacer])
    def legend():
        return Paragraph(' &nbsp; | &nbsp; '.join('<font color="'+PALETTE[i%len(PALETTE)]+'">'+escape(d.get('nick') or d['name'])+'</font>' for i,d in enumerate(drivers)),styles['Normal'])
    heading('RESULTS')
    data=[['POS','CHG','DRIVER','GAP SUM','GAP DELTA','LAPS','TOTAL','BEST LAP','TOP3 AVG','TOP3 CONSEC','P80 AVG','CONSISTENCY','KM/H']]
    for d in drivers:
        driver = (d.get('nick') or d['name']) + (' '+d['result_status'] if d.get('result_status') in ('DNS','DNF') else '')
        identity = f"#{d.get('no', d['pos'])} · TP {d.get('transponder', '-')}"
        driver_cell = Paragraph(escape(driver) + '<br/><font size="6" color="#65758a">' + escape(identity) + '</font>', styles['Normal'])
        data.append([d['pos'],f"{d.get('change',0):+d}",driver_cell,gap(d.get('gap_leader')),gap(d.get('gap_previous')),len(d['laps']),duration(d.get('total_ms')),duration(d.get('best_ms')),duration(d.get('top3_ms')),duration(d.get('consecutive_ms')),duration(d.get('p80_ms')),f"{d['consistency']:.1f}%" if d.get('consistency') is not None else '-',f"{d['top_speed_kmh']:.2f}" if d.get('top_speed_kmh') is not None else '-'])
    weights = [25,28,110,50,50,30,60,55,55,76,60,75,55]
    story += [table(data,[usable*v/sum(weights) for v in weights],6.2),Spacer(1,12)]
    heading('LAP TIMES')
    for start in range(0,len(drivers),9):
        group=drivers[start:start+9]
        data=[['LAP']+[para(f"#{d.get('no',d['pos'])} {d.get('nick') or d['name']}") for d in group]]
        for lap in range(max((len(d['laps']) for d in group),default=0)):
            data.append([lap+1]+[duration(d['laps'][lap]) if lap<len(d['laps']) else '-' for d in group])
        story += [table(data,[35]+[(usable-35)/len(group)]*len(group)),Spacer(1,10)]
    story.append(para('P80 average: mean of fastest 80% of full laps, rounded up. Consistency: max(0, 1 - population standard deviation / mean) x 100. The first decoder crossing is a baseline and is not counted as a completed lap.'))
    for title,kind in [('POSITION PROGRESSION','positions'),('GAP TO LEADER (SECONDS)','gaps')]:
        story.append(PageBreak());heading(title);story += [line_chart(drivers,kind,cfg),legend()]
    story.append(PageBreak());heading('LAP TIME DISTRIBUTION (WITH MEDIANS)')
    for start in range(0,len(drivers),10):
        if start:story.append(PageBreak());heading('LAP TIME DISTRIBUTION - CONTINUED')
        story.append(distribution(drivers[start:start+10],cfg))
    story.append(para('Light range: minimum to maximum. Dark range: 25th to 75th percentile. Vertical mark: median.'))
    def decorate(canvas,doc):
        canvas.setFillColor(colors.HexColor('#f7f8fa'));canvas.setStrokeColor(colors.HexColor('#dce0e5'))
        canvas.roundRect(28,height-55,width-56,36,6,fill=1,stroke=1)
        canvas.setFillColor(colors.HexColor('#182b40'));canvas.setFont('Helvetica-Bold',12)
        canvas.drawString(40,height-34,str(race.get('name','Quick Race'))[:85])
        canvas.setFont('Helvetica',8)
        target = str(cfg.get('value', '-')) + (' laps' if cfg.get('target') == 'laps' else ' seconds')
        stamp = datetime.fromtimestamp(race['started_at']).strftime('%d %b %Y %H:%M') if race.get('started_at') else '-'
        canvas.drawString(40,height-47,f"{race.get('track_name') or 'Track'} | {cfg.get('start_mode','grid').upper()} | {target} | {len(drivers)} drivers | {stamp}"[:145])
        canvas.setFillColor(colors.HexColor('#65758a'));canvas.setFont('Helvetica',7)
        canvas.drawString(28,16,f"YurLaps | Offline results | GO decoder: {race.get('start_decoder_ts', '-')} ms | {race.get('finish_reason', 'Complete')}"[:155])
        canvas.drawRightString(width-28,16,str(doc.page))
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
    return stream.getvalue()
