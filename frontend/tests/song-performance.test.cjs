const test = require("node:test");
const assert = require("node:assert/strict");
const { mountComponent } = require("./component-harness.cjs");
const flush = () => new Promise(resolve => setImmediate(resolve));
class TestFormData {
  constructor() { this.values = new Map(); }
  append(name, value) { this.values.set(name, value); }
  get(name) { return this.values.get(name); }
}
const words = [{index:0,text:"hola",status:"matched",start:.1,end:.5,estimated:false},
  {index:1,text:"mundo",status:"missing",start:.5,end:1.5,estimated:true}];
const data = {lyrics:"hola mundo",language:"es",song_duration:3,backing:{status:"missing"},exports:[],
  selected:{read:"reading",sing:"singing"},takes:[
    {id:"reading",mode:"read",status:"review",duration:3,song_start:0,words,audio_url:"/reading.wav",stale:false},
    {id:"singing",mode:"sing",status:"review",duration:3,song_start:20,words,audio_url:"/singing.wav",stale:false},
  ]};
function mount(request, globals={}) {
  return mountComponent("src/components/SongPerformance.tsx", {
    props:{song:{song_id:"test",name:"test.wav",language:"es"},model:{status:"trained",approved:true},onRecording:()=>{}},
    request, modules:{"@/lib/performance-recording":{startPerformanceCapture:()=>{throw new Error("No microphone in test");}}},
    globals:{FormData:TestFormData, URL:{revokeObjectURL:()=>{},createObjectURL:()=>"blob:test"}, ...globals},
  });
}
const wordButton = view => view.find(node => node.type==="button" && node.props.className==="word missing");

test("reading and singing have independent review, and isolated repeat carries parent and word index",async()=>{
  let sent;
  const view = mount((path, options)=>{
    if (options?.method==="POST") { sent={path,form:options.body}; return Promise.resolve(data); }
    return Promise.resolve(data);
  });
  await flush(); wordButton(view).props.onClick();
  assert.equal(view.button("Repetir solo «mundo»").props.disabled,false);
  const input=view.find(node=>node.props["aria-label"]==="Importar repetición de palabra");
  input.props.onChange({target:{files:[{size:1000,name:"word.wav"}],value:"word.wav"}});
  await flush();
  assert.equal(sent.path,"/performances/test/takes");
  assert.equal(sent.form.get("parent_id"),"reading");
  assert.equal(sent.form.get("word_index"),"1");
  assert.equal(sent.form.get("mode"),"read");
  view.button("Cantar letra").props.onClick();
  assert.match(view.text(),/Revisión de canto/);
  assert.equal(view.find(node=>node.props["aria-label"]==="Interpretación a revisar").props.value,"singing");
});

test("singing repetition requires headphones and reviewed timing, while import works over LAN HTTP",async()=>{
  const view=mount(()=>Promise.resolve(data), {window:{isSecureContext:false}});
  await flush(); view.button("Cantar letra").props.onClick(); wordButton(view).props.onClick();
  assert.equal(view.button("Repetir solo «mundo»").props.disabled,true);
  assert.match(view.text(),/Micrófono bloqueado por HTTP/);
  assert.equal(view.find(node=>node.props["aria-label"]==="Importar repetición de palabra").props.disabled,true);
  // The interval acknowledgement is the second checkbox in the singing editor.
  const boxes=view.nodes().filter(node=>node.type==="input" && node.props.type==="checkbox");
  boxes[1].props.onChange({target:{checked:true}});
  assert.equal(view.find(node=>node.props["aria-label"]==="Importar repetición de palabra").props.disabled,false);
  assert.equal(view.button("Repetir solo «mundo»").props.disabled,true);
});

test("unsaved lyrics block recording and polling cannot discard local text",async()=>{
  const view=mount(()=>Promise.resolve(data));
  await flush();
  view.find(node=>node.props["aria-label"]==="Letra de la canción").props.onChange({target:{value:"texto cambiado"}});
  assert.equal(view.button("Grabar lectura").props.disabled,true);
  view.poll(); await flush();
  assert.equal(view.find(node=>node.props["aria-label"]==="Letra de la canción").props.value,"texto cambiado");
  assert.match(view.text(),/Cambios sin guardar/);
});

test("failed word upload remains visible after a successful poll",async()=>{
  const view=mount((path,options)=>options?.method==="POST" ? Promise.reject(new Error("Audio inválido")) : Promise.resolve(data));
  await flush(); wordButton(view).props.onClick();
  view.find(node=>node.props["aria-label"]==="Importar repetición de palabra").props.onChange({target:{files:[{size:100,name:"word.wav"}],value:""}});
  await flush(); view.poll(); await flush();
  assert.deepEqual(view.alerts(),["Audio inválido"]);
});

test("mix requires an approved current model and sends personal conversion settings",async()=>{
  const ready={...data,backing:{status:"ready"},takes:data.takes.map(take=>({...take,
    words:words.map(word=>({...word,status:"matched"}))}))};
  let sent;
  const view=mount((path,options)=>{
    if(options?.method==="POST") sent={path,body:JSON.parse(options.body)};
    return Promise.resolve(ready);
  });
  await flush();
  assert.equal(view.button("Aplicar mi modelo y mezclar").props.disabled,false);
  for(const model of [{status:"trained",approved:false},{status:"trained",approved:true,stale:true},
    {status:"training",approved:true}]) {
    view.setProps({model});
    assert.equal(view.button("Aplicar mi modelo y mezclar").props.disabled,true);
  }
  view.setProps({model:{status:"trained",approved:true}});
  await view.button("Aplicar mi modelo y mezclar").props.onClick();
  assert.equal(sent.path,"/performances/test/mix");
  assert.equal(sent.body.user_id,"luis_felipe");
  assert.equal(sent.body.take_id,"singing");
  assert.equal(sent.body.pitch_shift,0);
});

test("old direct mixes are labelled honestly and new model output can be auditioned alone",async()=>{
  const view=mount(()=>Promise.resolve({...data,exports:[
    {id:"old",status:"completed",take_id:"singing",url:"/old.mp3"},
    {id:"new",status:"completed",take_id:"singing",url:"/new.mp3",vocal_url:"/new.flac",
      conversion:{personal_model:true,model_version:"personal-test"}},
  ]}));
  await flush();
  assert.match(view.text(),/Mezcla directa anterior · Sin modelo/);
  assert.match(view.text(),/Modelo aplicado · personal-test/);
  assert.equal(view.find(node=>node.props["aria-label"]==="Mi voz procesada con RVC").props.src,"/new.flac");
  assert.equal(view.find(node=>node.props["aria-label"]==="Toma sin convertir").props.src,"/singing.wav");
});

test("processing shows the backend conversion stage and blocks another export",async()=>{
  const view=mount(()=>Promise.resolve({...data,exports:[
    {id:"new",status:"processing",take_id:"singing",progress:40,stage:"Convirtiendo con tu modelo RVC personal"},
  ]}));
  await flush();
  assert.match(view.text(),/Convirtiendo con tu modelo RVC personal/);
  assert.equal(view.find(node=>node.type==="progress").props.value,40);
  assert.equal(view.button("Aplicar mi modelo y mezclar").props.disabled,true);
});
