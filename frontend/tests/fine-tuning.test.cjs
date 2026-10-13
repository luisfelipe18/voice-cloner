const test = require("node:test");
const assert = require("node:assert/strict");
const {mountComponent} = require("./component-harness.cjs");
const flush = () => new Promise(resolve=>setImmediate(resolve));
const model = {status:"trained",approved:true,version:"personal-old",stale:true,
  fine_tune:{available:true,changed:true,new_samples:3}};
const studio = voice => ({samples:[],seconds:{es:400,en:200},singing_seconds:300,
  ready:true,missing:[],model:voice});
function mount(voice, post, props={}) {
  return mountComponent("src/components/VoiceCapture.tsx",{
    props:{onModel:()=>{},...props},
    request:(path,options)=>path.endsWith("/prompts") ? Promise.resolve({prompts:[]})
      : options?.method==="POST" ? post(path,JSON.parse(options.body || "{}")) : Promise.resolve(studio(voice)),
  });
}

test("fine-tuning submits additional epochs once and shows training immediately",async()=>{
  let resolve, calls=0, updated;
  const pending=new Promise(done=>{resolve=done;});
  const view=mount(model,(path,body)=>{
    calls++;
    assert.equal(path,"/studio/luis_felipe/train");
    assert.deepEqual(body,{mode:"fine_tune",epochs:70});
    return pending;
  },{onModel:value=>{updated=value;}});
  await flush();
  assert.match(view.text(),/3 tomas nuevas aceptadas/);
  const input=view.find(node=>node.props["aria-label"]==="Épocas de fine-tuning");
  assert.equal(input.props.value,50);
  input.props.onChange({target:{value:"70"}});
  const click=view.button("Actualizar voz (fine-tuning)").props.onClick;
  const task=click();click();
  assert.equal(calls,1);
  assert.equal(view.button("Actualizar voz (fine-tuning)").props.disabled,true);
  resolve({status:"training",approved:false,epoch:0,epochs:70,stage:"Preparando dataset"});
  await task;
  assert.equal(updated.status,"training");
  assert.ok(view.button("Cancelar entrenamiento"));
  assert.match(view.text(),/época 0\/70/);
});

test("unchanged samples or missing full checkpoints block fine-tuning",async()=>{
  for(const fine_tune of [{available:true,changed:false},
    {available:false,changed:true,reason:"Faltan checkpoints G/D."}]) {
    const view=mount({...model,fine_tune},()=>{throw new Error("must not submit");});
    await flush();
    assert.equal(view.button("Actualizar voz (fine-tuning)").props.disabled,true);
    assert.match(view.text(),fine_tune.available ? /Sin cambios en las muestras aceptadas/ : /Faltan checkpoints G\/D/);
  }
  const view=mount(model,()=>{});
  await flush();
  view.find(node=>node.props["aria-label"]==="Épocas de fine-tuning").props.onChange({target:{value:"9"}});
  assert.equal(view.button("Actualizar voz (fine-tuning)").props.disabled,true);
});

test("first training stays initial while previous-model restore uses its own endpoint",async()=>{
  const initial=mount({status:"missing",approved:false},(path,body)=>{
    assert.deepEqual(body,{mode:"initial",epochs:200});
    return Promise.resolve({status:"training",approved:false});
  });
  await flush();
  await initial.button("Entrenar voz personal").props.onClick();
  const restored=mount({...model,parent_version:"personal-old"},(path,body)=>{
    assert.equal(path,"/studio/luis_felipe/train/restore");
    return Promise.resolve({...model,stale:false,parent_version:undefined});
  });
  await flush();
  await restored.button("Volver al modelo anterior").props.onClick();
  assert.equal(restored.button("Volver al modelo anterior"),undefined);
});

test("paired preview requires listening to the updated voice, not the old model",async()=>{
  const view=mountComponent("src/components/SongConverter.tsx",{
    props:{model:{...model,approved:false,stale:false,preview:{status:"completed",url:"new.mp3",
      original_url:"original.wav",previous_url:"old.mp3"}},onModel:()=>{}},
    modules:{"./AudioCompare":{default:()=>null}},request:()=>Promise.resolve({songs:[],uploads:[]}),
  });
  await flush();
  const audio=view.nodes().filter(node=>node.type==="audio");
  assert.deepEqual(audio.map(node=>node.props.src),["original.wav","old.mp3","new.mp3"]);
  assert.match(view.text(),/Modelo anteriorVoz actualizada/);
  assert.equal(view.button("El parecido me convence: aprobar voz").props.disabled,true);
  assert.equal(audio[1].props.onPlay,undefined);
  audio[2].props.onPlay();
  assert.equal(view.button("El parecido me convence: aprobar voz").props.disabled,false);
});
