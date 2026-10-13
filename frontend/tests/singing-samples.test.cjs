const test = require("node:test");
const assert = require("node:assert/strict");
const {mountComponent} = require("./component-harness.cjs");
const flush = () => new Promise(resolve => setImmediate(resolve));
const guide = {id:"song-es", song_id:"source-es", name:"practice.wav", language:"es",
  start_seconds:40, duration:24, text:"Una nube cruza la ventana abierta", word_count:7,
  recommended_pitch:-2, range:{low_hz:140,median_hz:180,high_hz:260}, isolated_available:true};
const catalog = {status:"ready", guides:[guide,{...guide,id:"song-en",language:"en",song_id:"source-en"}],
  progress:100,warnings:[],songs:[]};
function deferred() {
  let resolve;
  const promise = new Promise(yes => {resolve=yes;});
  return {promise,resolve};
}
async function mount(request, capture=async()=>{throw new Error("Micrófono denegado");}) {
  const view = mountComponent("src/components/SingingSamples.tsx", {
    request, props:{language:"es",isTraining:false,onSample:()=>{},onBusy:()=>{}},
    modules:{"@/lib/performance-recording":{startPerformanceCapture:capture}},
    globals:{FormData,Blob,URL:{createObjectURL:()=>"blob:test-take",revokeObjectURL:()=>{}}},
  });
  await flush(); view.setProps({});
  return view;
}

test("import keeps the song identity, adjusted tone and corrected expected words", async () => {
  const saved=deferred(); let posted;
  const view=await mount((path,options)=>{
    if(options?.method === "POST") {posted=options.body;return saved.promise;}
    return Promise.resolve(catalog);
  });
  view.find(node=>node.props?.["aria-label"] === "Texto del fragmento").props.onChange({target:{value:"El cielo cambia lentamente"}});
  const fileInput=view.find(node=>node.props?.["aria-label"] === "Importar canto del fragmento");
  fileInput.props.onChange({target:{files:[Object.assign(new Blob(["clean microphone"]),{name:"my-voice.wav"})],value:""}});
  fileInput.props.onChange({target:{files:[Object.assign(new Blob(["duplicate"]),{name:"again.wav"})],value:""}});
  assert.equal(posted.get("prompt_id"),"song-es");
  assert.equal(posted.get("guide_id"),"song-es");
  assert.equal(posted.get("pitch_shift"),"-2");
  assert.equal(posted.get("mode"),"sing");
  assert.equal(posted.get("expected_text"),"El cielo cambia lentamente");
  assert.equal(await posted.get("audio").text(),"clean microphone");
  assert.equal(view.button("Grabar fragmento").props.disabled,true);
  saved.resolve({id:"queued-take",status:"queued"});
  await flush();
});

test("guided recording requires headphones and retains a failed save for retry", async () => {
  const finished=deferred(); let options, calls=0, posts=0;
  const view=await mount((path,request)=>{
    if(request?.method === "POST") {
      posts++;
      return posts === 1 ? Promise.reject(new Error("No se guardó la toma")) : Promise.resolve({id:"saved"});
    }
    return Promise.resolve(catalog);
  },async input=>{calls++;options=input;return {finished:finished.promise,stop:()=>{},cancel:()=>{}};});
  await view.button("Grabar fragmento").props.onClick();
  assert.equal(calls,0);
  view.find(node=>node.props?.["aria-label"] === "Uso auriculares").props.onChange({target:{checked:true}});
  const recording=view.button("Grabar fragmento").props.onClick();
  await flush();
  assert.equal(calls,1);
  assert.equal(options.seconds,24);
  assert.equal(options.guideStart,0);
  assert.match(options.guideUrl,/song-es\/audio\?pitch=-2&kind=mix$/);
  assert.match(view.text(),/Empieza en 3/);
  finished.resolve(new Blob(["my voice only"]));
  await recording;
  const save=view.button("Guardar y analizar");
  await save.props.onClick();
  assert.deepEqual(view.alerts(),["No se guardó la toma"]);
  assert.ok(view.button("Guardar y analizar"));
  await view.button("Guardar y analizar").props.onClick();
  assert.equal(posts,2);
  assert.equal(view.button("Guardar y analizar"),undefined);
});

test("preparation progress prevents duplicate preparation requests", async () => {
  const view=await mount(()=>Promise.resolve({...catalog,status:"preparing",stage:"Reconociendo fragmentos",progress:45}));
  assert.equal(view.button("Actualizar selección").props.disabled,true);
  assert.match(view.text(),/Reconociendo fragmentos · 45%/);
});
