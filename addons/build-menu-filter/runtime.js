/* Build-specific display-only constructor filter. Loaded by launch.py. */
const cfg = __CONFIG__;
const game = Process.getModuleByName('ReassemblyRelease.exe');
function guarded(rva) {
  const expected = cfg.signatures[rva];
  if (!expected) throw Error('Missing signature for ' + rva);
  const bytes = new Uint8Array(game.base.add(parseInt(rva, 16)).readByteArray(expected.length / 2));
  if (Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('') !== expected) throw Error('Unsupported instruction signature ' + rva);
  return game.base.add(parseInt(rva, 16));
}
const addresses = {};
for (const key of Object.keys(cfg.signatures)) addresses[key] = guarded(key);
const lookup = new NativeFunction(addresses['25b2e0'], 'pointer', ['uint']);
const statFunctions={};
for(const [name,rva] of Object.entries({mass:'5c940',health:'5c8e0',area:'5c8c0',buildTime:'5c9c0',dps:'232300'}))
  statFunctions[name]=new NativeFunction(addresses[rva],'float',['pointer']);
statFunctions.cost=new NativeFunction(addresses['231c80'],'int',['pointer']);
const weaponRange=new NativeFunction(addresses['232400'],'float',['pointer','uint64']);
const rebuild = new NativeFunction(addresses['9b140'], 'void', ['pointer', 'pointer']);
const allocate = new NativeFunction(addresses['2f5588'], 'pointer', ['uint64']);
const release = new NativeFunction(addresses['2f5580'], 'void', ['pointer', 'uint64']);
const editors = new Map(), metadata = new Map();
let active = null, nesting = false, pending = false, typing = false, hooksReady = false;
let category = 'All', source = 'All', query = '', hitBoxes = [], frames = 0, failures = 0;
let captureRequested = null, disabled = false, mouseCaptured = false;
let visible = cfg.settings.toolbarVisible !== false;
let metric=FilterModel.sorts.some(s=>s.key===cfg.settings.defaultSort)?cfg.settings.defaultSort:'Default';
let direction=cfg.settings.sortDirection==='asc'?'asc':'desc',sortMenu=false;
let layout={x:cfg.settings.toolbarX??null,y:cfg.settings.toolbarY??12,scale:cfg.settings.toolbarScale??1};
let geometry=null,gesture=null,cursorFrames=0;
function setMetric(value){metric=value;direction=value==='name'?'asc':'desc';pending=true;sortMenu=false;}
function readIds(vector) {
  const begin = vector.readPointer(), end = vector.add(8).readPointer(), count = end.sub(begin).toInt32() / 4;
  if (!Number.isInteger(count) || count < 0 || count > 20000) throw Error('Invalid palette vector');
  return Array.from({length: count}, (_, i) => begin.add(i * 4).readU32());
}
function getMetadata(id) {
  if (metadata.has(id)) return metadata.get(id);
  const sb = lookup(id);
  if (sb.isNull()) throw Error('Unknown palette block ' + id);
  const bt = sb.add(0x90).readPointer(), name = bt.add(0x10).readPointer();
  const group = bt.readS32(), provenance = cfg.provenance[id];
  const item = { id, name: name.isNull() ? '' : name.readUtf8String(), group,
    features: sb.add(0x28).readU64().toString(), durability: bt.add(0x1c).readFloat(),
    source: provenance ? provenance.key : 'faction:' + group,
    sourceLabel: provenance ? provenance.label : (cfg.factionLabels[group] || 'Faction ' + group) };
  item.categories = FilterModel.classify(item, cfg.settings);
  item.stats={};
  for(const key of ['mass','health','area','cost','buildTime'])item.stats[key]=statFunctions[key](sb);
  const has=n=>(BigInt(item.features)&(1n<<BigInt(n)))!==0n;
  // Offsets confirmed against the current executable's own stats formatter.
  item.stats.generation=has(2)?bt.add(0x64).readFloat():null;
  const powerStorage=bt.add(0x30).readFloat(),resources=sb.add(0x38).readFloat();
  item.stats.powerStorage=powerStorage>0?powerStorage:null;
  item.stats.resources=resources>0?resources:null;
  item.stats.thrust=has(1)?bt.add(0x34).readFloat():null;
  const shield=sb.add(0x88).readPointer();
  item.stats.shieldHealth=has(9)&&!shield.isNull()?shield.readFloat():null;
  const weapon=item.categories.includes('Weapons');
  item.stats.dps=weapon?statFunctions.dps(sb):null;
  item.stats.range=weapon?weaponRange(sb,0):null;
  metadata.set(id, item); return item;
}
function capture(record, vector) {
  record.original = vector;
  record.ids = readIds(vector);
  record.items = record.ids.map(getMetadata);
  pending = true;
  send({type: 'palette-ready', total: record.ids.length});
}
function restore(record) {
  if (!record.original) return;
  nesting = true;
  try { rebuild(record.palette, record.original); }
  finally { nesting = false; }
}
function apply(record) {
  if (!record.original || !record.palette.add(0x2c8).readPointer().isNull()) return;
  if (record.palette.add(0x3c0).readU32() !== 0) return;
  // Re-read unlocked IDs: no game-owned ID is removed or rewritten.
  const originalIds = readIds(record.original);
  if (JSON.stringify(originalIds) !== JSON.stringify(record.ids)) capture(record, record.original);
  const filter = {category, source, query};
  const shown = FilterModel.sortItems(record.items.filter(b => FilterModel.matches(b, filter)),metric,direction).map(b => b.id);
  if (source !== 'All' && !record.items.some(b => b.source === source)) { source = 'All'; pending = true; return; }
  if (category === 'All' && source === 'All' && !query && metric==='Default') {
    restore(record);
  } else {
    const capacity = Math.max(record.ids.length + 512, 512);
    if (!record.data || record.capacity < capacity) {
      if (record.data) release(record.data, record.capacity * 4);
      record.data = allocate(capacity * 4); record.capacity = capacity;
    }
    if (!record.vector) record.vector = Memory.alloc(24);
    shown.forEach((id, i) => record.data.add(i * 4).writeU32(id));
    record.vector.writePointer(record.data);
    record.vector.add(8).writePointer(record.data.add(shown.length * 4));
    record.vector.add(16).writePointer(record.data.add(record.capacity * 4));
    nesting = true;
    try { rebuild(record.palette, record.vector); } finally { nesting = false; }
  }
  record.shown = shown.length; pending = false;
  if (JSON.stringify(readIds(record.original)) !== JSON.stringify(originalIds)) throw Error('Source palette changed unexpectedly');
  send({type: 'filter-applied', category, source, query,metric,direction, shown: shown.length, total: record.ids.length, sourceUnchanged: true});
}
function fault(error) {
  disabled = true;
  failures++; send({type: 'filter-error', message: String(error)});
  if (active) { try { restore(active); } catch (_) {} }
  active = null; typing = false;
}
Interceptor.attach(addresses['9d690'], {
  onEnter(args) { this.editor = args[0]; },
  onLeave() {
    const record = {editor: this.editor, palette: this.editor.add(0xd8), original: null};
    editors.set(record.palette.toString(), record);
  }
});
Interceptor.attach(addresses['9b140'], {
  onEnter(args) { this.record = !nesting ? editors.get(args[0].toString()) : null; this.vector = args[1]; },
  onLeave() { if (this.record) { try { capture(this.record, this.vector); } catch (e) { fault(e); } } }
});
Interceptor.attach(addresses['9b350'], {
  onEnter(args) {
    this.record = editors.get(args[0].toString());
    const record = editors.get(args[0].toString());
    if (record && args[1].toInt32() !== 0) { restore(record); if (active === record) active = null; typing = false; }
  },
  onLeave() { if (this.record) pending = true; }
});
Interceptor.attach(addresses['9e080'], {
  onEnter(args) {
    this.record = editors.get(args[0].add(0xd8).toString());
    if (this.record) {
      // Destructor owns buttons, but never owns the palette's ID list.
      if (this.record.original) this.record.palette.add(0x2b8).writePointer(this.record.original);
      editors.delete(this.record.palette.toString());
      if (active === this.record) active = null;
      typing = false;
    }
  },
  onLeave() { if (this.record && this.record.data) release(this.record.data, this.record.capacity * 4); }
});
Interceptor.attach(addresses['9bc50'], {
  onEnter(args) {
    try {
      const record = editors.get(args[0].toString());
      if (disabled || !record || args[0].add(0x3c0).readU32() !== 0) return;
      active = record; record.lastFrame = Date.now();
      if (!record.original) capture(record, args[0].add(0x2b8).readPointer());
      if (!hooksReady) installSDL();
      if (pending) apply(record);
    } catch (e) { fault(e); }
  }
});
function sources() {
  return ['All', ...new Set(active ? active.items.map(b => b.source) : [])];
}
function cycleSource() { const values = sources(); source = values[(values.indexOf(source) + 1) % values.length]; pending = true; }
function cycleCategory() { category = FilterModel.categories[(FilterModel.categories.indexOf(category) + 1) % FilterModel.categories.length]; pending = true; }
function input(event) {
  if (!active || Date.now() - active.lastFrame > 300) return false;
  const type = event.readU32();
  if(type===0x400 && gesture){
    const mx=event.add(20).readS32(),my=event.add(24).readS32();
    if(gesture.kind==='move') {layout.x=gesture.x+mx-gesture.mx;layout.y=gesture.y+my-gesture.my;}
    else layout.scale=Math.max(.65,Math.min(1.6,gesture.scale+(mx-gesture.mx)/900));
    return false; // Let the game update its native cursor position during gestures.
  }
  if (type === 0x300 || type === 0x301) {
    const key = event.add(20).readS32(), mods = event.add(24).readU16();
    if (key === 1073741888) { // F7
      if(type===0x300 && !event.add(13).readU8()){visible=!visible;typing=false;sortMenu=false;}
      return true;
    }
    if(key===1073741889){ // F8 / Shift+F8
      if(type===0x300&&!event.add(13).readU8()){
        if(mods&3){direction=direction==='asc'?'desc':'asc';pending=true;}
        else setMetric(FilterModel.sorts[(FilterModel.sorts.findIndex(s=>s.key===metric)+1)%FilterModel.sorts.length].key);
      }return true;
    }
    if(sortMenu&&key===27){sortMenu=false;return true;}
    if (key === 1073741887) { // F6
      if (type === 0x300 && !event.add(13).readU8()) { if (mods & 3) cycleSource(); else cycleCategory(); }
      return true;
    }
    if ((mods & 0xc0) && key === 102) { if (type === 0x300) {typing = true;visible=true;} return true; }
    if (typing) {
      if (type === 0x300) {
        if (key === 27 || key === 13) typing = false;
        else if (key === 8) { query = Array.from(query).slice(0, -1).join(''); pending = true; }
      }
      return true;
    }
  }
  if (type === 0x303 && typing) {
    query = (query + event.add(12).readUtf8String()).slice(0, 80); pending = true; return true;
  }
  if (type === 0x401 || type === 0x402) {
    if(type===0x402&&gesture){gesture=null;mouseCaptured=false;send({type:'layout-changed',layout});return true;}
    if (type === 0x402 && mouseCaptured) { mouseCaptured = false; return true; }
    const x = event.add(20).readS32(), y = event.add(24).readS32();
    const hit = hitBoxes.find(h => x >= h.x && x < h.x + h.w && y >= h.y && y < h.y + h.h);
    if (hit) {
      if (type === 0x401 && event.add(16).readU8() === 1) {
        mouseCaptured = true;
        if (hit.kind === 'toggle') { visible=!visible;typing=false;sortMenu=false; }
        else if(hit.kind==='move'||hit.kind==='resize'){
          if(hit.kind==='move'&&event.add(18).readU8()>=2){
            layout={x:null,y:12,scale:1};send({type:'layout-changed',layout});return true;
          }
          gesture={kind:hit.kind,mx:x,my:y,x:geometry.x,y:geometry.y,scale:layout.scale};typing=false;sortMenu=false;
          layout.x=geometry.x;
        }
        else if(hit.kind==='sort'){sortMenu=!sortMenu;typing=false;}
        else if(hit.kind==='sortOption')setMetric(hit.value);
        else if(hit.kind==='direction'){direction=direction==='asc'?'desc':'asc';pending=true;}
        else if (hit.kind === 'category') { category = hit.value; pending = true; typing = false; }
        else if (hit.kind === 'source') { cycleSource(); typing = false; }
        else if (hit.kind === 'clear') { category = 'All'; source = 'All'; query = ''; metric='Default';pending = true; typing = false;sortMenu=false; }
        else if (hit.kind === 'search') typing = true;
      }
      return true;
    }
    if (type === 0x401) {typing = false;sortMenu=false;}
  }
  return false;
}
let sdl, windowSize, startText,currentWindow;
function installSDL() {
  hooksReady = true; sdl = Process.getModuleByName('SDL2.dll');
  windowSize = new NativeFunction(sdl.getExportByName('SDL_GetWindowSize'), 'void', ['pointer', 'pointer', 'pointer']);
  startText = new NativeFunction(sdl.getExportByName('SDL_StartTextInput'), 'void', []); startText();
  currentWindow=new NativeFunction(sdl.getExportByName('SDL_GL_GetCurrentWindow'),'pointer',[]);
  // Draw before the engine's original cursor pass, preserving its cursor intact.
  Interceptor.attach(addresses['119f70'],{
    onEnter(args){
      if(active&&args[0].equals(active.editor)&&Date.now()-active.lastFrame<300){
        try{draw(currentWindow());frames++;cursorFrames++;}catch(e){fault(e);}
      }
    }
  });
  Interceptor.attach(sdl.getExportByName('SDL_PollEvent'), {
    onEnter(args) { this.event = args[0]; },
    onLeave(ret) { if (ret.toInt32() === 1 && !this.event.isNull()) { try { if (input(this.event)) this.event.writeU32(0); } catch (e) { fault(e); } } }
  });
  Interceptor.attach(sdl.getExportByName('SDL_GL_SwapWindow'), {
    onEnter(args) {
      if (captureRequested&&cfg.testing) {try{captureFrame();}catch(e){fault(e);}}
    }
  });
  send({type: 'filter-input-ready'});
}

let gl = null, font = 0,fontScale=0;
function graphics() {
  const ogl = Process.getModuleByName('opengl32.dll');
  const proc = new NativeFunction(sdl.getExportByName('SDL_GL_GetProcAddress'), 'pointer', ['pointer']);
  const bind = (name, ret, args) => new NativeFunction(ogl.findExportByName(name) || proc(Memory.allocUtf8String(name)), ret, args);
  gl = {};
  const specs = {
    glPushAttrib:['void',['uint']], glPopAttrib:['void',[]], glMatrixMode:['void',['uint']],
    glPushMatrix:['void',[]], glPopMatrix:['void',[]], glLoadIdentity:['void',[]],
    glOrtho:['void',['double','double','double','double','double','double']],
    glDisable:['void',['uint']], glEnable:['void',['uint']], glBlendFunc:['void',['uint','uint']],
    glUseProgram:['void',['uint']], glGetIntegerv:['void',['uint','pointer']],
    glBegin:['void',['uint']], glEnd:['void',[]], glVertex2f:['void',['float','float']],
    glColor4f:['void',['float','float','float','float']], glRasterPos2f:['void',['float','float']],
    glGenLists:['uint',['int']], glDeleteLists:['void',['uint','int']],glListBase:['void',['uint']], glCallLists:['void',['int','uint','pointer']]
  };
  for (const [name, spec] of Object.entries(specs)) gl[name] = bind(name, ...spec);
  makeFont();
}
function makeFont(){
  const ogl=Process.getModuleByName('opengl32.dll');
  const gdi = Process.getModuleByName('gdi32.dll');
  const create = new NativeFunction(gdi.getExportByName('CreateFontA'), 'pointer', ['int','int','int','int','int','uint','uint','uint','uint','uint','uint','uint','uint','pointer']);
  const select = new NativeFunction(gdi.getExportByName('SelectObject'), 'pointer', ['pointer','pointer']);
  const remove = new NativeFunction(gdi.getExportByName('DeleteObject'), 'int', ['pointer']);
  const dc = new NativeFunction(ogl.getExportByName('wglGetCurrentDC'), 'pointer', [])();
  const use = new NativeFunction(ogl.getExportByName('wglUseFontBitmapsA'), 'int', ['pointer','uint','uint','uint']);
  const face = create(-Math.round(15*layout.scale),0,0,0,400,0,0,0,1,0,0,4,0,Memory.allocUtf8String('Arial'));
  if(font)gl.glDeleteLists(font,256);
  const previous = select(dc, face); font = gl.glGenLists(256);
  if (!font || !use(dc,0,256,font)) throw Error('Unable to initialize filter text');
  select(dc, previous); remove(face);
  fontScale=Math.round(15*layout.scale);
}
function rectangle(x,y,w,h,color) {
  gl.glColor4f(...color); gl.glBegin(7);
  for (const [a,b] of [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]) gl.glVertex2f(a,b);
  gl.glEnd();
}
function text(x,y,value,color=[.9,.94,1,1]) {
  const ascii=String(value).replace(/[^\x20-\x7e]/g,'?');
  gl.glColor4f(...color); gl.glRasterPos2f(x,y); gl.glListBase(font);
  gl.glCallLists(ascii.length,5121,Memory.allocUtf8String(ascii));
}
function panel(x,y,w,h,selected=false) {
  rectangle(x,y,w,h,selected?[.77,.66,.34,.95]:[.58,.58,.58,.95]);
  rectangle(x+1,y+1,w-2,h-2,selected?[.30,.25,.13,.96]:[.16,.16,.16,.96]);
}
function draw(window) {
  if (!gl) graphics();
  const dimensions=Memory.alloc(8);windowSize(window,dimensions,dimensions.add(4));
  const width=dimensions.readS32(),height=dimensions.add(4).readS32();
  layout.scale=Math.max(.65,Math.min(1.6,layout.scale,(width-16)/900));
  if(fontScale!==Math.round(15*layout.scale))makeFont();
  const scale=layout.scale;
  const physicalWidth=900*scale,physicalHeight=(visible?143:27)*scale;
  const px=Math.max(4,Math.min(layout.x??(width-physicalWidth)/2,width-physicalWidth-4));
  const py=Math.max(4,Math.min(layout.y,height-physicalHeight-4));
  if(layout.x!==null)layout.x=px;layout.y=py;
  geometry={x:px,y:py,width:physicalWidth,height:physicalHeight,scale,screenWidth:width,screenHeight:height};
  const oldProgram=Memory.alloc(4),oldMode=Memory.alloc(4);
  gl.glGetIntegerv(0x8b8d,oldProgram);gl.glGetIntegerv(0xba0,oldMode);
  gl.glPushAttrib(0xfffff);
  let projectionPushed=false,modelPushed=false;
  try {
    gl.glUseProgram(0);
    for(const cap of [2929,2960,3553,3089,2884,2896,3008])gl.glDisable(cap);
    gl.glEnable(3042);gl.glBlendFunc(770,771);
    gl.glMatrixMode(5889);gl.glPushMatrix();projectionPushed=true;gl.glLoadIdentity();gl.glOrtho(0,width/scale,height/scale,0,-1,1);
    gl.glMatrixMode(5888);gl.glPushMatrix();modelPushed=true;gl.glLoadIdentity();
    const x=px/scale,y=py/scale;
    const panelWidth=900,chipWidth=Math.floor((panelWidth-16)/5);
    hitBoxes=[];
    panel(x,y,visible?panelWidth:138,visible?143:27);
    text(x+10,y+18,visible?`PART FILTER  ${active.shown === undefined ? active.ids.length : active.shown}/${active.ids.length}    F6: category   Shift+F6: source   Ctrl+F: search`:'Filters  [F7]');
    const tx=visible?x+panelWidth-82:x;
    if(visible){panel(tx,y+2,74,20);text(tx+9,y+17,'Hide [F7]');}
    hitBoxes.push({x:tx,y,w:visible?82:138,h:24,kind:'toggle'});
    if(visible)hitBoxes.push({x,y,w:panelWidth-86,h:23,kind:'move'});
    if(visible){
    FilterModel.categories.forEach((value,i)=>{
      const bx=x+8+(i%5)*chipWidth,by=y+24+Math.floor(i/5)*25;
      panel(bx,by,chipWidth-4,22,value===category);
      text(bx+7,by+16,value);hitBoxes.push({x:bx,y:by,w:chipWidth-4,h:22,kind:'category',value});
    });
    const sy=y+77,searchWidth=Math.floor(panelWidth*.4),sourceWidth=panelWidth-searchWidth-86;
    panel(x+8,sy,searchWidth,26,typing);
    text(x+14,sy+18,(query ? query : 'Search name or ID')+(typing?' |':''));
    hitBoxes.push({x:x+8,y:sy,w:searchWidth,h:26,kind:'search'});
    const label=source==='All'?'All source factions':(active.items.find(b=>b.source===source)||{}).sourceLabel||source;
    panel(x+searchWidth+14,sy,sourceWidth,26,source!=='All');
    text(x+searchWidth+20,sy+18,String(label).slice(0,Math.floor(sourceWidth/8)));
    hitBoxes.push({x:x+searchWidth+14,y:sy,w:sourceWidth,h:26,kind:'source'});
    panel(x+panelWidth-64,sy,56,26);text(x+panelWidth-57,sy+18,'Reset');
    hitBoxes.push({x:x+panelWidth-64,y:sy,w:56,h:26,kind:'clear'});
    const sortY=y+108;
    panel(x+8,sortY,300,26,metric!=='Default');
    text(x+14,sortY+18,'Sort: '+FilterModel.sorts.find(s=>s.key===metric).label+'  [choose]');
    hitBoxes.push({x:x+8,y:sortY,w:300,h:26,kind:'sort'});
    panel(x+314,sortY,150,26);text(x+321,sortY+18,direction==='asc'?'Low to high / A-Z':'High to low / Z-A');
    hitBoxes.push({x:x+314,y:sortY,w:150,h:26,kind:'direction'});
    text(x+476,sortY+18,'F8: sort   Shift+F8: reverse');
    text(x+panelWidth-22,y+138,'//');
    hitBoxes.push({x:x+panelWidth-28,y:y+124,w:28,h:19,kind:'resize'});
    if(sortMenu){
      const rows=7,menuY=Math.min(y+147,(height-8)/scale-(rows*27+8));
      panel(x+8,menuY,604,rows*27+8);
      const menuHits=[];
      FilterModel.sorts.forEach((s,i)=>{
        const bx=x+12+Math.floor(i/rows)*300,by=menuY+4+(i%rows)*27;
        panel(bx,by,296,24,s.key===metric);text(bx+7,by+17,s.label);
        menuHits.push({x:bx,y:by,w:296,h:24,kind:'sortOption',value:s.key});
      });
      hitBoxes=menuHits.concat(hitBoxes);
    }
    }
    hitBoxes.push({x,y,w:visible?900:138,h:visible?143:27,kind:'panel'});
    hitBoxes=hitBoxes.map(h=>({...h,x:h.x*scale,y:h.y*scale,w:h.w*scale,h:h.h*scale}));
    gl.glMatrixMode(5888);gl.glPopMatrix();modelPushed=false;gl.glMatrixMode(5889);gl.glPopMatrix();projectionPushed=false;
  } finally {
    if(modelPushed){gl.glMatrixMode(5888);gl.glPopMatrix();}
    if(projectionPushed){gl.glMatrixMode(5889);gl.glPopMatrix();}
    gl.glPopAttrib();gl.glMatrixMode(oldMode.readU32());gl.glUseProgram(oldProgram.readU32());
  }
}
function captureFrame(){
    if(captureRequested && cfg.testing){
      const viewport=Memory.alloc(16);gl.glGetIntegerv(0xba2,viewport);
      const w=viewport.add(8).readS32(),h=viewport.add(12).readS32();
      if(w>0&&h>0&&w*h<20000000){
        const ogl=Process.getModuleByName('opengl32.dll');
        const getproc=new NativeFunction(sdl.getExportByName('SDL_GL_GetProcAddress'),'pointer',['pointer']);
        const bindbuffer=new NativeFunction(getproc(Memory.allocUtf8String('glBindBuffer')),'void',['uint','uint']);
        const store=new NativeFunction(ogl.getExportByName('glPixelStorei'),'void',['uint','int']);
        const read=new NativeFunction(ogl.getExportByName('glReadPixels'),'void',['int','int','int','int','uint','uint','pointer']);
        const push=new NativeFunction(ogl.getExportByName('glPushClientAttrib'),'void',['uint']);
        const pop=new NativeFunction(ogl.getExportByName('glPopClientAttrib'),'void',[]);
        const oldbuffer=Memory.alloc(4);gl.glGetIntegerv(0x88ed,oldbuffer);push(1);
        const pixels=Memory.alloc(w*h*4);
        try{bindbuffer(0x88eb,0);store(3333,1);store(3330,0);store(3331,0);store(3332,0);
          read(viewport.readS32(),viewport.add(4).readS32(),w,h,6408,5121,pixels);
          send({type:'screenshot',name:captureRequested,width:w,height:h},pixels.readByteArray(w*h*4));
        }finally{pop();bindbuffer(0x88eb,oldbuffer.readU32());}
      }
      captureRequested=null;
    }
}
rpc.exports = {
  steamstatus() {
    if(!cfg.testing)throw Error('Test-only operation');
    if(startupSteam!==true)return {initialized:false};
    const api=Process.getModuleByName('steam_api64.dll');
    const storage=new NativeFunction(api.getExportByName('SteamAPI_SteamRemoteStorage_v016'),'pointer',[])();
    const exists=new NativeFunction(api.getExportByName('SteamAPI_ISteamRemoteStorage_FileExists'),'bool',['pointer','pointer']);
    const count=new NativeFunction(api.getExportByName('SteamAPI_ISteamRemoteStorage_GetFileCount'),'int',['pointer']);
    return {initialized:true,callbacksReady:startupReady,fileCount:count(storage),
      saveSlots:[0,1,2].map(slot=>exists(storage,Memory.allocUtf8String('data/save'+slot+'/save.lua')))};
  },
  setfilter(value) {
    if (!FilterModel.categories.includes(value.category) || typeof value.query !== 'string' || typeof value.source !== 'string') throw Error('Invalid filter');
    category=value.category;query=value.query.slice(0,80);source=value.source;pending=true;return true;
  },
  setsort(value){
    if(!FilterModel.sorts.some(s=>s.key===value.metric)||!['asc','desc'].includes(value.direction))throw Error('Invalid sort');
    metric=value.metric;direction=value.direction;pending=true;return true;
  },
  testitems(){
    if(!cfg.testing||!active)throw Error('Test-only operation');
    return readIds(active.palette.add(0x2b8).readPointer()).map(id=>getMetadata(id));
  },
  status() { return {category,source,query,typing,visible,metric,direction,pending,geometry,cursorFrames,frames,failures,active:!!active,total:active?active.ids.length:0,shown:active?active.shown:0}; }
  ,capture(name) { if(!cfg.testing)throw Error('Test-only operation');captureRequested=String(name);return true; }
  ,testevent(value) {
    if(!cfg.testing||!hooksReady)throw Error('Test-only operation');
    const event=Memory.alloc(56);event.writeByteArray(new Uint8Array(56));
    event.writeU32(value.type);
    if(value.type===0x300||value.type===0x301){event.add(20).writeS32(value.key);event.add(24).writeU16(value.mods||0);}
    else if(value.type===0x303){event.add(12).writeUtf8String(String(value.text).slice(0,25));}
    else if(value.type===0x401||value.type===0x402){event.add(16).writeU8(1);event.add(18).writeU8(value.clicks||1);event.add(20).writeS32(value.x);event.add(24).writeS32(value.y);}
    else if(value.type===0x400){event.add(16).writeU32(1);event.add(20).writeS32(value.x);event.add(24).writeS32(value.y);}
    else throw Error('Unsupported test event');
    return new NativeFunction(sdl.getExportByName('SDL_PushEvent'),'int',['pointer'])(event);
  }
  ,sources() { return sources().map(key=>({key,label:key==='All'?'All':active.items.find(b=>b.source===key).sourceLabel})); }
};
send({type:'filter-extension-ready'});
