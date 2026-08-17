// HopMKT_T6W3.pptx — Họp Tuần MKT Eye Plus | Review W3 (15-21/6) · Plan W4 (22-30/6)
// Dựng: 22/06/2026
const pptxgen = require('pptxgenjs');
const p = new pptxgen();
p.layout = 'LAYOUT_WIDE';
p.title = 'Họp MKT Tuần 4 — Eye Plus T6/2026';

const C = {
  navy:'12305C', navy2:'1E4B8F', red:'C40D2E', redDk:'8E0A20',
  ice:'D6E4F7', gold:'D4A017', green:'1E8E3E', orange:'E27D2A',
  gray:'9CA3AF', white:'FFFFFF', bg:'F4F6FB', text:'1A1A2E',
  muted:'5B6472', light:'EAEEF7', cardLine:'D5DCEA',
  amber:'F59E0B', teal:'0D9488', purple:'7C3AED',
};
const F='Calibri'; const W=13.33, H=7.5;

// ---------- helpers ----------
const header=(s,badge,title)=>{
  s.background={color:C.bg};
  s.addShape('rect',{x:0,y:0,w:W,h:0.62,fill:{color:C.navy}});
  s.addShape('rect',{x:0,y:0.62,w:W,h:0.05,fill:{color:C.red}});
  s.addText(title,{x:0.55,y:0.05,w:9.5,h:0.5,fontSize:18,fontFace:F,color:C.white,bold:true,valign:'middle'});
  s.addText(badge,{x:10.3,y:0.05,w:2.5,h:0.5,fontSize:12,fontFace:F,color:C.ice,bold:true,align:'right',valign:'middle'});
  s.addText('LƯU HÀNH NỘI BỘ  |  EYE PLUS  |  Họp MKT Tuần 4 — 23/06/2026',
    {x:0.55,y:H-0.34,w:9,h:0.28,fontSize:9,fontFace:F,color:C.muted,italic:true});
};
const divider=(num,big,sub)=>{
  const s=p.addSlide(); s.background={color:C.navy};
  s.addShape('rect',{x:0,y:0,w:0.35,h:H,fill:{color:C.red}});
  s.addText(num,{x:0.55,y:1.5,w:3.8,h:3.5,fontSize:190,fontFace:F,color:C.red,bold:true});
  s.addText(big,{x:4.3,y:2.3,w:8.5,h:1.3,fontSize:52,fontFace:F,color:C.white,bold:true});
  s.addText(sub,{x:4.35,y:3.7,w:8.5,h:0.6,fontSize:19,fontFace:F,color:C.ice});
  return s;
};
const hCell=(t,o={})=>({text:t,options:{fill:{color:o.bg||C.navy},color:C.white,bold:true,align:o.align||'center',fontSize:o.fs||11}});
const cell=(t,o={})=>({text:t,options:{align:o.align||'center',bold:!!o.b,color:o.c||C.text,fill:o.fill?{color:o.fill}:undefined,fontSize:o.fs||11}});
const tbl=(s,x,y,w,rows,colW,opts={})=>{
  s.addTable(rows,{x,y,w,colW,fontSize:opts.fs||11,fontFace:F,color:C.text,valign:'middle',
    border:{type:'solid',color:C.cardLine,pt:0.5},align:'left',autoPage:false,rowH:opts.rowH||0.32});
};
const sBox=(s,x,y,w,h,val,label,color,sub='')=>{
  s.addShape('roundRect',{x,y,w,h,fill:{color:C.white},line:{color:color,width:2},rectRadius:0.07,
    shadow:{type:'outer',blur:5,offset:2,angle:90,color:'C0C8D8',opacity:0.3}});
  s.addShape('rect',{x,y,w,h:0.06,fill:{color:color}});
  s.addText(val,{x,y:y+0.12,w,h:h*0.46,fontSize:26,fontFace:F,color:color,bold:true,align:'center',valign:'middle'});
  s.addText(label,{x:x+0.08,y:y+h*0.54,w:w-0.16,h:h*0.26,fontSize:10,fontFace:F,color:C.muted,align:'center'});
  if(sub) s.addText(sub,{x:x+0.08,y:y+h*0.8,w:w-0.16,h:h*0.18,fontSize:9,fontFace:F,color:color,align:'center',bold:true});
};
const bars=(s,x,y,w,h,title,groups,legend)=>{
  if(title) s.addText(title,{x,y,w,h:0.3,fontSize:12,fontFace:F,color:C.navy,bold:true,align:'center'});
  const cy=y+0.36, ch=h-0.96, baseY=cy+ch;
  let max=0; groups.forEach(g=>g.bars.forEach(b=>{if(b.v>max)max=b.v;})); max*=1.18;
  const n=groups.length, gw=w/n, nb=groups[0].bars.length, pad=gw*0.14, bw=(gw-2*pad)/nb;
  s.addShape('line',{x,y:baseY,w,h:0,line:{color:C.gray,width:1}});
  groups.forEach((g,gi)=>{
    const gx=x+gi*gw;
    g.bars.forEach((b,bi)=>{
      const bh=ch*(b.v/max), bx=gx+pad+bi*bw;
      s.addShape('rect',{x:bx+0.04,y:baseY-bh,w:bw-0.08,h:bh,fill:{color:b.c}});
      s.addText(b.d,{x:bx-0.1,y:baseY-bh-0.27,w:bw+0.2,h:0.25,fontSize:9,fontFace:F,color:C.text,bold:true,align:'center'});
    });
    s.addText(g.label,{x:gx,y:baseY+0.06,w:gw,h:0.32,fontSize:10,fontFace:F,color:C.text,align:'center'});
  });
  if(legend){let lx=x+w-legend.length*2.0;legend.forEach((lg,i)=>{
    s.addShape('rect',{x:lx+i*2.0,y:y+0.03,w:0.18,h:0.18,fill:{color:lg.c}});
    s.addText(lg.t,{x:lx+i*2.0+0.24,y:y-0.03,w:1.7,h:0.28,fontSize:9.5,fontFace:F,color:C.text});});}
};

// ============ SLIDE 1 — TITLE ============
let s=p.addSlide(); s.background={color:C.navy};
s.addShape('rect',{x:8.6,y:0,w:4.73,h:H,fill:{color:C.red}});
s.addText('PHÒNG MARKETING',{x:0.7,y:0.55,w:7,h:0.4,fontSize:13,fontFace:F,color:C.ice,bold:true,charSpacing:3});
s.addText('HỌP TUẦN 4',{x:0.7,y:2.4,w:8,h:1.5,fontSize:66,fontFace:F,color:C.white,bold:true});
s.addText('Tháng 06 / 2026',{x:0.72,y:3.92,w:8,h:0.65,fontSize:26,fontFace:F,color:C.ice});
s.addText('Ngày họp: 23/06/2026  ·  Review W3 (15–21/6)  ·  Plan W4 (22–30/6)',
  {x:0.72,y:4.62,w:8,h:0.4,fontSize:14,fontFace:F,color:C.ice});
s.addText('04',{x:8.85,y:1.5,w:4.2,h:3.7,fontSize:230,fontFace:F,color:C.redDk,bold:true,align:'center'});

// ============ SLIDE 2 — AGENDA ============
s=p.addSlide(); header(s,'W4 · AGENDA','Agenda — 30% Review W3  /  70% Plan W4');
const agL=[
  '01 · So sánh DT bán lẻ 3 tuần (thực tế vs mục tiêu)',
  '02 · Diễn biến từng ngày W3 + phân tích chi phí full',
  '03 · Quảng cáo tự động vs nhắm tay — kết quả W3',
  '04 · Phân bổ ngân sách W4 — giữ mức, không vượt trần',
  '05 · Content công thức + Phân nhiệm sản xuất',
];
const agR=[
  '06 · Mục tiêu W4: 2,092tr / 9 ngày',
  '07 · 8 nhiệm vụ trọng tâm tuần cuối tháng',
  '08 · Báo cáo ứng dụng AI hàng ngày — triển khai W4',
  '09 · Khó khăn → Giải pháp + Người phụ trách + Deadline',
  '10 · Timeline tuần + Chốt hành động',
];
s.addShape('rect',{x:0.45,y:0.82,w:5.9,h:5.9,fill:{color:C.white},line:{color:C.cardLine,width:1},shadow:{type:'outer',blur:4,offset:2,angle:90,color:'C0CBD8',opacity:0.25}});
s.addShape('rect',{x:6.95,y:0.82,w:5.9,h:5.9,fill:{color:C.white},line:{color:C.cardLine,width:1},shadow:{type:'outer',blur:4,offset:2,angle:90,color:'C0CBD8',opacity:0.25}});
s.addShape('rect',{x:0.45,y:0.82,w:5.9,h:0.44,fill:{color:C.navy}});
s.addShape('rect',{x:6.95,y:0.82,w:5.9,h:0.44,fill:{color:C.red}});
s.addText('TUẦN CŨ — W3 REVIEW (30%)',{x:0.55,y:0.84,w:5.7,h:0.38,fontSize:12,fontFace:F,color:C.white,bold:true,valign:'middle'});
s.addText('TUẦN MỚI — W4 PLAN (70%)',{x:7.05,y:0.84,w:5.7,h:0.38,fontSize:12,fontFace:F,color:C.white,bold:true,valign:'middle'});
agL.forEach((t,i)=>{
  s.addText(t,{x:0.62,y:1.42+i*0.92,w:5.6,h:0.78,fontSize:12.5,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.1});
  if(i<4) s.addShape('line',{x:0.62,y:2.14+i*0.92,w:5.5,h:0,line:{color:C.cardLine,width:0.5}});
});
agR.forEach((t,i)=>{
  s.addText(t,{x:7.1,y:1.42+i*0.92,w:5.6,h:0.78,fontSize:12.5,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.1});
  if(i<4) s.addShape('line',{x:7.1,y:2.14+i*0.92,w:5.5,h:0,line:{color:C.cardLine,width:0.5}});
});

// ============ DIVIDER 1 — TUẦN CŨ ============
divider('01','TUẦN CŨ','Review W3 · 15–21/06/2026');

// ============ SLIDE 4 — DT 3 TUẦN ============
s=p.addSlide(); header(s,'W3 · KẾT QUẢ','So sánh doanh thu bán lẻ — 3 tuần T6/2026');

// Stat boxes
sBox(s,0.42,0.82,2.9,1.55,'1,599tr','W1 (1–7/6) · MT 1,694tr',C.teal,'94.4% ✓');
sBox(s,3.55,0.82,2.9,1.55,'1,844tr','W2 (8–14/6) · MT 2,162tr',C.orange,'85.3%');
sBox(s,6.68,0.82,2.9,1.55,'1,613tr','W3 (15–21/6) · MT 2,062tr',C.red,'78.2% ↓');
sBox(s,9.81,0.82,2.9,1.55,'2,092tr','W4 MT (22–30/6) · 9 ngày',C.navy,'Mục tiêu');

// Chart
bars(s,0.42,2.55,12.5,3.5,null,[
  {label:'W1 (1–7/6)',bars:[{v:1694,d:'1,694tr',c:C.ice},{v:1599,d:'1,599tr',c:C.teal}]},
  {label:'W2 (8–14/6)',bars:[{v:2162,d:'2,162tr',c:C.ice},{v:1844,d:'1,844tr',c:C.orange}]},
  {label:'W3 (15–21/6)',bars:[{v:2062,d:'2,062tr',c:C.ice},{v:1613,d:'1,613tr',c:C.red}]},
  {label:'W4 (22–30/6)',bars:[{v:2092,d:'2,092tr',c:C.ice},{v:0,d:'',c:C.bg}]},
],[{t:'Mục tiêu',c:C.ice},{t:'Thực tế',c:C.teal}]);

// Table
tbl(s,0.42,6.1,12.5,[
  [hCell(''),hCell('W1 (1–7/6)'),hCell('W2 (8–14/6)'),hCell('W3 (15–21/6)'),hCell('W4 MT (22–30/6)')],
  [cell('Mục tiêu',{align:'left',b:true}),cell('1,694tr',{c:C.teal,b:true}),cell('2,162tr',{c:C.orange,b:true}),cell('2,062tr',{c:C.red,b:true}),cell('2,092tr',{c:C.navy,b:true})],
  [cell('Thực tế',{align:'left'}),cell('1,599tr'),cell('1,844tr'),cell('1,613tr'),cell('—',{c:C.muted})],
  [cell('% đạt',{align:'left',b:true}),cell('94.4%',{c:C.teal,b:true}),cell('85.3%',{c:C.orange,b:true}),cell('78.2%',{c:C.red,b:true}),cell('—')],
  [cell('Số đơn',{align:'left'}),cell('1,334'),cell('1,406'),cell('1,280'),cell('—')],
  [cell('Chi quảng cáo (full)',{align:'left'}),cell('210.0tr'),cell('278.2tr'),cell('248.5tr'),cell('≤344tr (còn lại)')],
  [cell('% chi / DT',{align:'left',b:true}),cell('13.1%'),cell('15.1%'),cell('15.4%',{c:C.red,b:true}),cell('≤16.5%')],
  [cell('Giá tin trung bình',{align:'left'}),cell('55,300đ'),cell('60,300đ'),cell('48,000đ'),cell('—')],
],[1.9,1.9,1.9,1.9,1.9],{rowH:0.3});

// ============ SLIDE 5 — DIỄN BIẾN TỪNG NGÀY W3 ============
s=p.addSlide(); header(s,'W3 · NGÀY','Diễn biến từng ngày W3 (15–21/6) — DT + Chi phí quảng cáo');

const dayRows=[
  [hCell('Ngày'),hCell('DT bán lẻ'),hCell('Số đơn'),hCell('Chi FB'),hCell('Chi TikTok'),hCell('Chi GG'),hCell('Chi FULL'),hCell('Ghi chú')],
  [cell('T2 15/6',{align:'left',b:true}),cell('251.1tr',{c:C.teal,b:true}),cell('193'),cell('33.5tr'),cell('1.65tr'),cell('0'),cell('35.2tr',{b:true}),cell('Cao nhất tuần',{align:'left',c:C.teal})],
  [cell('T3 16/6',{align:'left'}),cell('207.9tr',{c:C.red}),cell('163'),cell('37.2tr'),cell('2.1tr'),cell('0'),cell('39.3tr',{b:true,c:C.red}),cell('Chi cao, DT thấp',{align:'left',c:C.red})],
  [cell('T4 17/6',{align:'left'}),cell('210.6tr'),cell('172'),cell('31.9tr'),cell('2.0tr'),cell('0'),cell('34.0tr',{b:true}),cell('',{align:'left'})],
  [cell('T5 18/6',{align:'left'}),cell('203.2tr',{c:C.red}),cell('162'),cell('31.7tr'),cell('1.8tr'),cell('0'),cell('33.4tr',{b:true}),cell('Thấp thứ 2',{align:'left',c:C.red})],
  [cell('T6 19/6',{align:'left'}),cell('259.2tr',{c:C.teal,b:true}),cell('177'),cell('29.2tr'),cell('1.6tr'),cell('0'),cell('30.9tr',{b:true}),cell('Chi ít, DT cao',{align:'left',c:C.teal})],
  [cell('T7 20/6',{align:'left'}),cell('236.2tr'),cell('201'),cell('26.2tr'),cell('1.5tr'),cell('0'),cell('27.7tr',{b:true}),cell('Đơn cuối tuần tốt',{align:'left'})],
  [cell('CN 21/6',{align:'left'}),cell('245.2tr'),cell('212'),cell('31.5tr'),cell('1.6tr'),cell('1.3tr'),cell('34.4tr',{b:true}),cell('GG bật lại',{align:'left'})],
  [hCell('TỔNG W3'),hCell('1,613tr'),hCell('1,280'),hCell('221.2tr'),hCell('12.3tr'),hCell('1.3tr'),hCell('234.8tr'),hCell('')],
];
tbl(s,0.38,0.82,12.6,dayRows,[1.2,1.35,0.9,1.3,1.3,1.05,1.3,2.2],{rowH:0.61});

s.addText('⚡ T6 19/6: DT cao nhất (259tr) nhưng chi phí thấp nhất (30.9tr) → hiệu suất tốt nhất tuần  |  T3 16/6: ngược lại — chi 39.3tr chỉ đổi được 207.9tr DT',
  {x:0.38,y:6.85,w:12.6,h:0.42,fontSize:10,fontFace:F,color:C.navy,bold:false,italic:true,align:'center'});

// ============ SLIDE 6 — TRẠNG THÁI ĐẦU VIỆC ============
s=p.addSlide(); header(s,'W3 · DONE','Trạng thái đầu việc W3 — Tổng kết');
sBox(s,0.42,0.82,3.0,1.4,'6/6','Checklist hoàn thành',C.teal,'All done ✓');
sBox(s,3.6,0.82,3.0,1.4,'6','Cần hỗ trợ W3',C.orange,'Đã ghi nhận');
sBox(s,6.78,0.82,3.0,1.4,'6','Việc chưa xong → W4',C.red,'Chuyển tiếp');
sBox(s,9.96,0.82,3.0,1.4,'↑W4','Ưu tiên xử lý',C.navy,'Tuần cuối tháng');

// Pending items table
tbl(s,0.42,2.42,5.9,[
  [hCell('VIỆC CHƯA XONG → CHUYỂN W4',{bg:C.red})],
  [cell('Lan: Workflow Nhanh cho content',{align:'left'})],
  [cell('Loan: Video test gọng đập mạnh + quay tay',{align:'left'})],
  [cell('Quyên: Video oto + concept KOC + buổi quay cầm tay',{align:'left'})],
  [cell('Tùng: Sát sao HP & BN',{align:'left'})],
  [cell('Đạt: Tool KOC source + chốt deal cast',{align:'left'})],
],[5.9],{rowH:0.5});

tbl(s,6.65,2.42,6.2,[
  [hCell('HỌC ĐƯỢC / ĐÚC KẾT TUẦN W3',{bg:C.teal})],
  [cell('Đạt: Rule giữ/tắt cam = chi phí/đơn (không chỉ giá tin) — mess rẻ có thể là rác',{align:'left'})],
  [cell('Loan: TikTok nên làm content so sánh / world cup / có ý nghĩa hơn',{align:'left'})],
  [cell('Quyên: Page "chợ" → lọc kĩ nội dung, chạy chất không chạy số lượng',{align:'left'})],
  [cell('Trang: Brief rõ ràng → edit nhanh hơn',{align:'left'})],
  [cell('Tùng: Xong buổi học AI thứ 2',{align:'left'})],
],[6.2],{rowH:0.5});

// ============ SLIDE 7 — CHI PHÍ THEO % DT ============
s=p.addSlide(); header(s,'W4 · CHI PHÍ','Chi phí quảng cáo tính theo % doanh thu — nguyên tắc W4');

// Key principle box
s.addShape('roundRect',{x:0.42,y:0.82,w:12.5,h:1.08,fill:{color:C.navy},rectRadius:0.07});
s.addText('NGUYÊN TẮC: Chi phí quảng cáo = % × DT thực tế — DT cao được chi nhiều, DT thấp phải cắt tương ứng, KHÔNG giữ số cứng',
  {x:0.6,y:0.9,w:12.2,h:0.75,fontSize:13,fontFace:F,color:C.white,bold:true,align:'center',valign:'middle'});

// % comparison chart (bar chart showing % per week)
bars(s,0.42,2.05,6.0,3.2,'% Chi phí / DT thực tế — 3 tuần T6',[
  {label:'W1 (1–7/6)\nDT 1,599tr',bars:[{v:13.1,d:'13.1%',c:C.teal}]},
  {label:'W2 (8–14/6)\nDT 1,844tr',bars:[{v:15.1,d:'15.1%',c:C.orange}]},
  {label:'W3 (15–21/6)\nDT 1,613tr',bars:[{v:14.6,d:'14.6%',c:C.red}]},
  {label:'W4 MỤC TIÊU\nDT 2,092tr',bars:[{v:14.5,d:'≤14.5%',c:C.navy}]},
],null);
// Target line label
s.addShape('line',{x:0.42,y:3.68,w:6.0,h:0,line:{color:C.teal,width:1.5,dashType:'dash'}});
s.addText('Mục tiêu ≤14.5%',{x:0.45,y:3.7,w:3,h:0.25,fontSize:9,fontFace:F,color:C.teal,bold:true});

tbl(s,6.65,2.05,6.25,[
  [hCell('Tuần'),hCell('Chi FULL'),hCell('DT bán lẻ'),hCell('% chi/DT'),hCell('Đánh giá')],
  [cell('W1 (1–7/6)',{align:'left'}),cell('210.0tr'),cell('1,599tr'),cell('13.1%',{c:C.teal,b:true}),cell('✓ Đạt',{c:C.teal,b:true})],
  [cell('W2 (8–14/6)',{align:'left'}),cell('278.2tr'),cell('1,844tr'),cell('15.1%',{c:C.orange,b:true}),cell('⚠ Hơi cao',{c:C.orange})],
  [cell('W3 (15–21/6)',{align:'left'}),cell('234.8tr'),cell('1,613tr'),cell('14.6%',{c:C.red,b:true}),cell('⚠ Hơi cao',{c:C.red})],
  [cell('W4 MỤC TIÊU',{align:'left',b:true,c:C.navy}),cell('≤303tr',{c:C.navy,b:true}),cell('2,092tr',{b:true,c:C.navy}),cell('≤14.5%',{b:true,c:C.navy}),cell('Cần giữ',{c:C.navy,b:true})],
],[1.5,1.4,1.4,1.35,1.4],{rowH:0.5});

s.addText('Nếu DT thực W4 thấp hơn 2,092tr → chi phải giảm tương ứng để giữ ≤14.5%\nVí dụ: DT thực 1,900tr → chi tối đa 14.5% × 1,900 = 275.5tr',
  {x:6.65,y:4.72,w:6.25,h:0.75,fontSize:11,fontFace:F,color:C.navy,italic:true,lineSpacingMultiple:1.25});

tbl(s,0.42,5.38,12.5,[
  [hCell(''),hCell('FB'),hCell('TikTok'),hCell('GG'),hCell('Chi FULL'),hCell('DT bán lẻ'),hCell('% chi/DT'),hCell('Giá tin'),hCell('Tổng tin')],
  [cell('W1',{align:'left'}),cell('194.6tr'),cell('3.3tr'),cell('12.2tr'),cell('210.0tr',{b:true}),cell('1,599tr'),cell('13.1%',{c:C.teal,b:true}),cell('55,300đ'),cell('3,799')],
  [cell('W2',{align:'left'}),cell('258.2tr'),cell('8.1tr'),cell('11.9tr'),cell('278.2tr',{b:true}),cell('1,844tr'),cell('15.1%',{c:C.orange,b:true}),cell('60,300đ'),cell('4,616')],
  [cell('W3',{align:'left'}),cell('221.2tr'),cell('12.3tr'),cell('1.3tr'),cell('234.8tr',{b:true}),cell('1,613tr'),cell('14.6%',{c:C.red,b:true}),cell('48,000đ'),cell('5,175')],
  [hCell('W4 Target'),hCell('—'),hCell('—'),hCell('—'),hCell('≤303tr'),hCell('2,092tr'),hCell('≤14.5%'),hCell('—'),hCell('—')],
],[0.45,1.38,1.38,1.1,1.4,1.4,1.2,1.3,1.0],{rowH:0.3});

s.addText('⚠️ TikTok tăng 3.7× từ W1→W3 — theo dõi ROAS TikTok riêng để biết % này có hiệu quả không.',
  {x:0.42,y:7.2,w:12.5,h:0.26,fontSize:10,fontFace:F,color:C.muted,italic:true,align:'center'});

// ============ SLIDE 8 — ADV vs MANUAL ============
s=p.addSlide(); header(s,'W3 · ADS','Quảng cáo tự động (ADV) vs Nhắm tay — kết quả W3');

s.addText('Phân tích dựa trên dữ liệu camp 15–21/6 (398 camp đang chạy)',
  {x:0.42,y:0.78,w:12.5,h:0.3,fontSize:11,fontFace:F,color:C.muted,italic:true});

tbl(s,0.42,1.2,6.0,[
  [hCell('Chỉ số',{align:'left'}),hCell('Tự động (ADV)'),hCell('Nhắm tay (Manual)')],
  [cell('Số lượng camp',{align:'left'}),cell('141 (35.4%)'),cell('257 (64.6%)')],
  [cell('Chi phí TB/camp',{align:'left'}),cell('—',{c:C.muted}),cell('—',{c:C.muted})],
  [cell('ROAS TB',{align:'left'}),cell('~3.5x',{c:C.teal}),cell('~4.1x',{c:C.teal})],
  [cell('Giá tin TB',{align:'left'}),cell('Thấp hơn',{c:C.teal}),cell('Cao hơn',{c:C.orange})],
  [cell('Kết luận',{align:'left',b:true}),cell('Tin rẻ hơn',{c:C.teal}),cell('Chuyển đổi tốt hơn',{c:C.orange})],
],[2.2,1.8,1.8],{rowH:0.42});

tbl(s,6.65,1.2,6.1,[
  [hCell('CTKM'),hCell('Gợi ý'),hCell('Lý do')],
  [cell('CT1 Summer Boom 39K',{align:'left'}),cell('Nhắm tay',{c:C.orange,b:true}),cell('Neo giá rõ, cần tệp đúng',{align:'left'})],
  [cell('CT2 Multi-Look 50%',{align:'left'}),cell('Tự động',{c:C.teal,b:true}),cell('Tệp rộng, ADV tốt',{align:'left'})],
  [cell('CT3 Đổi màu+Râm 20%',{align:'left'}),cell('Nhắm tay',{c:C.orange,b:true}),cell('ROAS thấp ở ADV W3',{align:'left'})],
  [cell('CT4 Eyezen+Chemi 450K',{align:'left'}),cell('Nhắm tay',{c:C.orange,b:true}),cell('Giá cao cần tư vấn',{align:'left'})],
  [cell('CT5 Kính râm 20%',{align:'left'}),cell('Tự động',{c:C.teal,b:true}),cell('Tệp nữ rộng',{align:'left'})],
  [cell('CT6 Voucher đo mắt',{align:'left'}),cell('Nhắm tay ✓✓',{c:C.teal,b:true}),cell('ROAS 9.4x W3 — giữ',{align:'left',c:C.teal})],
],[1.6,1.5,2.8],{rowH:0.42});

s.addText('💡 CT6 Voucher đo mắt: ROAS 9.4x, 38 đơn từ chỉ 51 tin — công thức tốt nhất tuần → duy trì & tăng ngân sách W4',
  {x:0.42,y:5.85,w:12.5,h:0.55,fontSize:12,fontFace:F,color:C.teal,bold:true,align:'center',
  line:{color:C.teal,width:1.5},inset:0.15});

// Camp hiệu quả cao W3
tbl(s,0.42,6.5,12.2,[
  [hCell('Top camp hiệu quả W3 (ROAS > 4.5x)',{bg:C.teal}),hCell('Chi'),hCell('Tin'),hCell('Đơn'),hCell('ROAS')],
  [cell('CT6 — Voucher đo mắt nhận voucher HN (Tùng)',{align:'left'}),cell('3.8tr'),cell('51'),cell('38',{b:true}),cell('9.4x',{c:C.teal,b:true})],
  [cell('GC vid — HCM nữ 25-35 (Tùng)',{align:'left'}),cell('3.3tr'),cell('123'),cell('15'),cell('7.2x',{c:C.teal,b:true})],
  [cell('Ảnh chạm trắng — HN nam 25-35 (Tùng)',{align:'left'}),cell('3.9tr'),cell('69'),cell('15'),cell('5.9x',{c:C.teal,b:true})],
  [cell('GC vid — HCM nam 25-35 (Tùng)',{align:'left'}),cell('3.5tr'),cell('116'),cell('16'),cell('6.4x',{c:C.teal,b:true})],
],[5.5,1.5,1.5,1.5,1.5],{rowH:0.3});

// ============ SLIDE 9 — CONTENT WIN ============
s=p.addSlide(); header(s,'W3 · CONTENT','Content công thức W3 + Phân nhiệm sản xuất W4');

// Left: Content insights
s.addShape('roundRect',{x:0.42,y:0.82,w:5.9,h:5.6,fill:{color:C.white},line:{color:C.cardLine,width:1},rectRadius:0.06});
s.addShape('rect',{x:0.42,y:0.82,w:5.9,h:0.44,fill:{color:C.navy}});
s.addText('CÔNG THỨC CONTENT W3',{x:0.55,y:0.84,w:5.7,h:0.38,fontSize:12,fontFace:F,color:C.white,bold:true,valign:'middle'});
const wins=[
  {icon:'🏆',title:'CT6 Voucher đo mắt',body:'Ảnh đơn + call-to-action rõ → 51 tin / 38 đơn. ROAS 9.4x.\nKhông cần tin nhiều, cần đúng người.'},
  {icon:'📸',title:'Ảnh chạm nền trắng (HN nam)',body:'Sản phẩm nổi bật, không rối → ROAS 5.9x.\nCông thức: nền sạch + neo giá rõ.'},
  {icon:'🎬',title:'Video lifestyle HCM',body:'GC vid ngắn, khách hàng thực → ROAS 6-7x.\nTệp nữ 25-35 phản hồi tốt.'},
  {icon:'❌',title:'Tránh: mess rẻ mà không ra đơn',body:'Tin <50K + chi >300K không đơn → tắt ngay.\n(Đạt đúc kết W3 — đã áp dụng.)'},
];
wins.forEach((w,i)=>{
  const y=1.37+i*1.1;
  s.addText(w.icon+' '+w.title,{x:0.58,y,w:5.55,h:0.32,fontSize:12,fontFace:F,color:C.navy,bold:true});
  s.addText(w.body,{x:0.65,y:y+0.3,w:5.45,h:0.65,fontSize:10.5,fontFace:F,color:C.text,lineSpacingMultiple:1.15});
  if(i<3) s.addShape('line',{x:0.58,y:y+1.0,w:5.55,h:0,line:{color:C.cardLine,width:0.5}});
});

// Right: Phân nhiệm W4
s.addShape('roundRect',{x:6.65,y:0.82,w:6.25,h:5.6,fill:{color:C.white},line:{color:C.cardLine,width:1},rectRadius:0.06});
s.addShape('rect',{x:6.65,y:0.82,w:6.25,h:0.44,fill:{color:C.red}});
s.addText('PHÂN NHIỆM SẢN XUẤT W4',{x:6.78,y:0.84,w:6.1,h:0.38,fontSize:12,fontFace:F,color:C.white,bold:true,valign:'middle'});
tbl(s,6.65,1.32,6.25,[
  [hCell('Nhiệm vụ',{align:'left'}),hCell('Người phụ trách'),hCell('Deadline'),hCell('KPI')],
  [cell('Video test gọng đập mạnh',{align:'left'}),cell('Loan'),cell('25/6'),cell('≥3 video')],
  [cell('Video quay tay gọng mới',{align:'left'}),cell('Loan'),cell('27/6'),cell('≥2 video')],
  [cell('Video oto + đẩy TikTok CT5',{align:'left'}),cell('Quyên'),cell('25/6'),cell('≥5 video')],
  [cell('Lookbook AI kính râm CT5',{align:'left'}),cell('Tùng + Quyên'),cell('26/6'),cell('1 bộ 6 ảnh')],
  [cell('Ảnh concept mới thay KOC đơn',{align:'left'}),cell('Quyên + Trang'),cell('27/6'),cell('≥4 concept')],
  [cell('Workflow Nhanh cho content',{align:'left'}),cell('Lan'),cell('25/6'),cell('1 tài liệu')],
  [cell('Buổi quay cầm tay kính CT4',{align:'left'}),cell('Quyên + Trang'),cell('28/6'),cell('≥3 video')],
  [cell('Duyệt ảnh trước khi lên bài',{align:'left'}),cell('Trang duyệt'),cell('Daily'),cell('0 ảnh "chợ"')],
],[2.5,1.6,1.2,0.8],{rowH:0.42});

// ============ SLIDE 10 — KHÓ KHĂN ============
s=p.addSlide(); header(s,'W3 · KHÓ KHĂN','Khó khăn W3 → Giải pháp + Người phụ trách + Deadline W4');
tbl(s,0.38,0.82,12.6,[
  [hCell('Khó khăn',{align:'left'}),hCell('Người'),hCell('Giải pháp W4',{align:'left'}),hCell('Người phụ trách'),hCell('Deadline')],
  [cell('Cần CapCut Pro để xuất video chất lượng',{align:'left'}),cell('Quyên'),cell('Mua tài khoản CapCut Pro cho team content',{align:'left'}),cell('TP MKT'),cell('23/6')],
  [cell('Quy trình hợp đồng KOC chưa rõ',{align:'left'}),cell('Đạt'),cell('Đạt liên hệ chị Ngọc nắm rõ quy trình → ghi lại SOP',{align:'left'}),cell('Đạt'),cell('25/6')],
  [cell('KOC Bắc Ninh cũ đã loãng — cần thay',{align:'left'}),cell('Tùng'),cell('Tùng sát sao tìm KOC BN mới + Đạt hỗ trợ source',{align:'left'}),cell('Tùng + Đạt'),cell('27/6')],
  [cell('Cần lookbook AI kính râm CT5 cho tệp tỉnh',{align:'left'}),cell('Tùng'),cell('Tạo bộ lookbook AI 6 ảnh → chạy thử BN/HP',{align:'left'}),cell('Tùng + Quyên'),cell('26/6')],
  [cell('Page bị feedback "chợ" — content quá nhiều, kém chất',{align:'left'}),cell('Quyên'),cell('Giảm tần suất đăng, Trang duyệt hình trước khi lên',{align:'left'}),cell('Quyên + Trang'),cell('Daily')],
  [cell('Loan chưa chốt được khách tại cửa hàng',{align:'left'}),cell('Loan'),cell('Học hỏi thêm quy trình tư vấn offline, đặc biệt khách nữ',{align:'left'}),cell('Loan'),cell('28/6')],
],[2.65,0.8,3.8,1.75,0.95],{rowH:0.48});

// ============ DIVIDER 2 — TUẦN MỚI ============
divider('02','TUẦN MỚI','Plan W4 · 22–30/06/2026 · 9 ngày đóng tháng');

// ============ SLIDE 12 — MỤC TIÊU W4 ============
s=p.addSlide(); header(s,'W4 · MỤC TIÊU','Mục tiêu Tuần 4 — W4 (22–30/6) · Tuần đóng tháng');

sBox(s,0.42,0.82,3.7,1.5,'2,092tr','Mục tiêu DT bán lẻ W4',C.navy,'9 ngày · 232tr/ngày');
sBox(s,4.3,0.82,3.7,1.5,'≤14.5%','Tỉ lệ chi/DT mục tiêu W4',C.red,'Chi = 14.5% × DT thực tế mỗi ngày');
sBox(s,8.18,0.82,3.7,1.5,'≤303tr','Tối đa nếu đạt MT (14.5%×2,092)',C.orange,'Nếu DT thấp → chi phải giảm theo');

// Daily target table W4
tbl(s,0.42,2.5,12.5,[
  [hCell('Ngày'),hCell('T2 22/6'),hCell('T3 23/6'),hCell('T4 24/6'),hCell('T5 25/6'),hCell('T6 26/6'),hCell('T7 27/6'),hCell('CN 28/6'),hCell('T2 29/6'),hCell('T3 30/6')],
  [cell('MT DT (tr)',{align:'left',b:true}),cell('200',{c:C.teal}),cell('210'),cell('215'),cell('220'),cell('225'),cell('270',{c:C.orange,b:true}),cell('280',{c:C.orange,b:true}),cell('220'),cell('252',{c:C.red,b:true})],
  [cell('Chi QC tối đa (14.5%)',{align:'left',c:C.red}),cell('29tr',{c:C.red}),cell('30tr',{c:C.red}),cell('31tr',{c:C.red}),cell('32tr',{c:C.red}),cell('33tr',{c:C.red}),cell('39tr',{c:C.orange,b:true}),cell('41tr',{c:C.orange,b:true}),cell('32tr',{c:C.red}),cell('37tr',{c:C.red})],
  [cell('Đơn MT',{align:'left'}),cell('158'),cell('166'),cell('170'),cell('174'),cell('178'),cell('214'),cell('222'),cell('174'),cell('200')],
],[1.4,1.27,1.27,1.27,1.27,1.27,1.27,1.27,1.27,1.27],{rowH:0.42});

s.addText('Chi QC tối đa = 14.5% × DT thực tế ngày hôm đó — nếu DT thấp hơn mục tiêu thì chi phải giảm tương ứng, không giữ số cứng.',
  {x:0.42,y:4.22,w:12.5,h:0.38,fontSize:11,fontFace:F,color:C.red,italic:true,bold:true});

// Budget allocation chart
bars(s,0.42,4.72,12.5,2.3,null,[
  {label:'T2 22/6',bars:[{v:200,d:'200tr',c:C.teal}]},
  {label:'T3 23/6',bars:[{v:210,d:'210tr',c:C.teal}]},
  {label:'T4 24/6',bars:[{v:215,d:'215tr',c:C.teal}]},
  {label:'T5 25/6',bars:[{v:220,d:'220tr',c:C.teal}]},
  {label:'T6 26/6',bars:[{v:225,d:'225tr',c:C.teal}]},
  {label:'T7 27/6',bars:[{v:270,d:'270tr',c:C.orange}]},
  {label:'CN 28/6',bars:[{v:280,d:'280tr',c:C.orange}]},
  {label:'T2 29/6',bars:[{v:220,d:'220tr',c:C.teal}]},
  {label:'T3 30/6',bars:[{v:252,d:'252tr',c:C.red}]},
],null);

// ============ SLIDE 13 — 8 NHIỆM VỤ TRỌNG TÂM ============
s=p.addSlide(); header(s,'W4 · KẾ HOẠCH','8 nhiệm vụ trọng tâm W4 — tuần đóng tháng');
tbl(s,0.38,0.82,12.6,[
  [hCell('#'),hCell('Nhiệm vụ',{align:'left'}),hCell('Người phụ trách'),hCell('KPI'),hCell('Deadline')],
  [cell('1',{b:true,c:C.red}),cell('Kiểm soát tỉ lệ chi/DT — giữ ≤ 14.5% mỗi ngày, không vượt 303tr W4',{align:'left',b:true}),cell('Tùng + Đạt'),cell('chi/DT ≤ 14.5% hàng ngày'),cell('30/6')],
  [cell('2',{b:true}),cell('Tắt camp mess rẻ + không ra đơn (>300K / 0 đơn)',{align:'left'}),cell('Đạt'),cell('Daily review'),cell('Hàng ngày')],
  [cell('3',{b:true}),cell('Giữ và scale camp hiệu quả: CT6 voucher, ảnh trắng HN',{align:'left'}),cell('Tùng'),cell('ROAS ≥ 4x'),cell('Hàng ngày')],
  [cell('4',{b:true}),cell('Sát sao HP (Lạch Tray) + BN — báo cáo ngày',{align:'left'}),cell('Tùng'),cell('Báo cáo hàng ngày'),cell('23/6')],
  [cell('5',{b:true}),cell('Triển khai báo cáo ứng dụng AI hàng ngày (MKT team)',{align:'left'}),cell('Tất cả'),cell('Điền đủ 6/6 người'),cell('23/6')],
  [cell('6',{b:true}),cell('KOC: chốt deal cast Đạt + tìm KOC BN mới (Tùng)',{align:'left'}),cell('Đạt + Tùng'),cell('≥1 KOC chốt'),cell('27/6')],
  [cell('7',{b:true}),cell('Content chất lượng: Trang duyệt ảnh trước khi đăng',{align:'left'}),cell('Quyên + Trang'),cell('0 ảnh "chợ"'),cell('Daily')],
  [cell('8',{b:true}),cell('TikTok: tăng video có ý nghĩa (so sánh, chất liệu, cuộc sống)',{align:'left'}),cell('Loan + Đạt'),cell('≥5 video/tuần'),cell('28/6')],
],[0.45,4.6,1.85,2.5,1.05],{rowH:0.5});

// ============ SLIDE 14 — ĐÀO TẠO + AI REPORT ============
s=p.addSlide(); header(s,'W4 · ĐÀO TẠO','Đào tạo AI đã xong — W4: Gắn ứng dụng vào công việc thực tế');

// Card 1: Kết quả đào tạo
s.addShape('roundRect',{x:0.42,y:0.82,w:3.9,h:5.8,fill:{color:C.white},line:{color:C.cardLine,width:1},rectRadius:0.07,shadow:{type:'outer',blur:5,offset:2,angle:90,color:'C0C8D8',opacity:0.3}});
s.addShape('rect',{x:0.42,y:0.82,w:3.9,h:0.5,fill:{color:C.teal}});
s.addText('✅ ĐÃ ĐÀO TẠO XONG',{x:0.55,y:0.84,w:3.7,h:0.44,fontSize:13,fontFace:F,color:C.white,bold:true,valign:'middle'});
s.addText([
  {text:'Buổi 1: ',options:{bold:true,color:C.navy}},{text:'AI cơ bản, tools cơ bản\n'},
  {text:'Buổi 2: ',options:{bold:true,color:C.navy}},{text:'AI video (Veo3), AI ảnh\n'},
  {text:'\nTất cả 6 thành viên team MKT đã tham gia.\n\n'},
  {text:'Vấn đề: ',options:{bold:true,color:C.red}},{text:'Chưa có cơ chế kiểm soát cách ứng dụng AI vào công việc thực tế hàng ngày.'},
],{x:0.58,y:1.44,w:3.6,h:4.0,fontSize:12,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.3});

// Card 2: Báo cáo AI hàng ngày
s.addShape('roundRect',{x:4.5,y:0.82,w:4.0,h:5.8,fill:{color:C.white},line:{color:C.cardLine,width:1},rectRadius:0.07,shadow:{type:'outer',blur:5,offset:2,angle:90,color:'C0C8D8',opacity:0.3}});
s.addShape('rect',{x:4.5,y:0.82,w:4.0,h:0.5,fill:{color:C.navy}});
s.addText('📋 BÁO CÁO ỨNG DỤNG AI (MỚI)',{x:4.63,y:0.84,w:3.8,h:0.44,fontSize:13,fontFace:F,color:C.white,bold:true,valign:'middle'});
s.addText([
  {text:'Từ W4 (23/6): ',options:{bold:true,color:C.red}},{text:'mỗi thành viên điền báo cáo ứng dụng AI mỗi ngày\n\n'},
  {text:'Nội dung báo cáo:\n',options:{bold:true}},
  {text:'• Hôm nay dùng AI tool gì?\n'},
  {text:'• Tạo ra gì? (ảnh / video / text / ý tưởng)\n'},
  {text:'• Tiết kiệm được bao nhiêu thời gian?\n'},
  {text:'• Kết quả có dùng được không?\n\n'},
  {text:'Mục tiêu: ',options:{bold:true,color:C.navy}},{text:'Gắn AI với kết quả digital MKT — đo lường thực tế.'},
],{x:4.65,y:1.44,w:3.7,h:4.2,fontSize:12,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.3});

// Card 3: AI tools được dùng
s.addShape('roundRect',{x:8.68,y:0.82,w:4.2,h:5.8,fill:{color:C.white},line:{color:C.cardLine,width:1},rectRadius:0.07,shadow:{type:'outer',blur:5,offset:2,angle:90,color:'C0C8D8',opacity:0.3}});
s.addShape('rect',{x:8.68,y:0.82,w:4.2,h:0.5,fill:{color:C.red}});
s.addText('🤖 AI TOOLS ĐỀ XUẤT W4',{x:8.81,y:0.84,w:4.0,h:0.44,fontSize:13,fontFace:F,color:C.white,bold:true,valign:'middle'});
s.addText([
  {text:'Ảnh & thiết kế: ',options:{bold:true,color:C.navy}},{text:'Midjourney, Leonardo, Canva AI\n\n'},
  {text:'Video: ',options:{bold:true,color:C.navy}},{text:'Veo3 (Google), CapCut Pro AI, Kling\n\n'},
  {text:'Caption & content: ',options:{bold:true,color:C.navy}},{text:'ChatGPT, Claude\n\n'},
  {text:'Lookbook: ',options:{bold:true,color:C.navy}},{text:'AI background remover + product placement\n\n'},
  {text:'Phân tích ads: ',options:{bold:true,color:C.navy}},{text:'Dashboard camp + Claude phân tích rule tắt/giữ'},
],{x:8.83,y:1.44,w:3.9,h:4.2,fontSize:12,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.3});

// ============ SLIDE 15 — TIMELINE + CHỐT ============
s=p.addSlide(); header(s,'W4 · CHỐT','Timeline Tuần 4 + Chốt hành động');

// Timeline
const days=[
  {d:'T2\n23/6',c:C.navy,tasks:['Họp triển khai W4','Tất cả điền báo cáo AI','Đạt: review camp, tắt rác']},
  {d:'T3\n24/6',c:C.navy2,tasks:['Lan: hoàn thành workflow Nhanh','Đạt: liên hệ chị Ngọc (KOC HĐ)','Tùng: sát sao HP + BN']},
  {d:'T4\n25/6',c:C.navy2,tasks:['Loan: video test gọng đập','Tùng: lookbook AI CT5 v1','Quyên: video oto đẩy TikTok']},
  {d:'T5\n26/6',c:C.orange,tasks:['Quyên+Trang: concept KOC mới','Review camp giữa tuần','Tùng: lookbook finalize']},
  {d:'T6\n27/6',c:C.orange,tasks:['Đạt: chốt deal cast KOC','Loan: quay tay gọng mới','Review báo cáo AI tuần']},
  {d:'T7\n28/6',c:C.red,tasks:['Tùng: tăng budget camp tốt','Quyên+Trang: buổi quay cầm tay','Push ads T7 — đỉnh tuần']},
  {d:'CN\n29/6',c:C.red,tasks:['Push CN — đỉnh tuần thứ 2','Monitor chi/DT real-time','Check phân bổ ngân sách còn']},
  {d:'T2-T3\n30/6',c:C.redDk,tasks:['Đóng tháng — check target','Báo cáo tổng kết T6','Chuẩn bị họp W1 T7']},
];
days.forEach((d,i)=>{
  const x=0.38+i*1.62, y=0.82;
  s.addShape('rect',{x,y,w:1.52,h:0.55,fill:{color:d.c}});
  s.addText(d.d,{x,y:y+0.02,w:1.52,h:0.5,fontSize:11,fontFace:F,color:C.white,bold:true,align:'center',valign:'middle'});
  s.addShape('rect',{x:x+0.72,y:y+0.55,w:0.08,h:0.25,fill:{color:d.c}});
  s.addShape('roundRect',{x,y:y+0.8,w:1.52,h:3.1,fill:{color:C.white},line:{color:d.c,width:1.5},rectRadius:0.05});
  d.tasks.forEach((t,j)=>{
    s.addText('• '+t,{x:x+0.08,y:y+0.88+j*0.95,w:1.36,h:0.88,fontSize:9.5,fontFace:F,color:C.text,valign:'top',lineSpacingMultiple:1.1});
    if(j<2) s.addShape('line',{x:x+0.08,y:y+1.78+j*0.95,w:1.36,h:0,line:{color:C.cardLine,width:0.5}});
  });
});

// Chốt box
s.addShape('roundRect',{x:0.38,y:5.0,w:12.58,h:2.18,fill:{color:C.navy},rectRadius:0.08});
s.addText('CHỐT 3 VIỆC PHẢI LÀM TRƯỚC KHI RỜI PHÒNG HỌP HÔM NAY',
  {x:0.55,y:5.06,w:12.2,h:0.36,fontSize:13,fontFace:F,color:C.ice,bold:true,align:'center'});
s.addShape('line',{x:0.55,y:5.44,w:12.2,h:0,line:{color:C.red,width:1}});
const chots=[
  {n:'1',t:'Tùng + Đạt xác nhận: chi/DT ≤ 14.5% mỗi ngày — DT thực thấp thì chi phải cắt, không giữ số cứng'},
  {n:'2',t:'Tất cả 6 thành viên đồng ý điền báo cáo ứng dụng AI từ ngày 23/6'},
  {n:'3',t:'Đạt liên hệ chị Ngọc về quy trình KOC trước 25/6 — Tùng bắt đầu tìm KOC BN mới'},
];
chots.forEach((c,i)=>{
  s.addShape('rect',{x:0.5+i*4.19,y:5.52,w:0.5,h:1.35,fill:{color:C.red}});
  s.addText(c.n,{x:0.5+i*4.19,y:5.52,w:0.5,h:1.35,fontSize:18,fontFace:F,color:C.white,bold:true,align:'center',valign:'middle'});
  s.addText(c.t,{x:1.08+i*4.19,y:5.56,w:3.6,h:1.25,fontSize:11.5,fontFace:F,color:C.white,valign:'middle',lineSpacingMultiple:1.2});
});

// ============ EXPORT ============
const outPath='/Users/hungnguyen/Công Việc/AI/fb_ad_local/HopMKT_T6W4_23062026.pptx';
p.writeFile({fileName:outPath}).then(()=>{
  console.log('✅ Done:', outPath);
}).catch(e=>console.error('❌',e));
