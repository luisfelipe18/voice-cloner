const test = require("node:test");
const assert = require("node:assert/strict");
const {mountComponent} = require("./component-harness.cjs");
const flush = () => new Promise(resolve=>setImmediate(resolve));
const trained={status:"trained",approved:false};
const songs=[{song_id:"song1",name:"first-song.mp3",language:"en",status:"pending"},
  {song_id:"song2",name:"second-song.mp3",language:"es",status:"pending"}];
const AudioCompare=()=>null;

test("trained-model shortcut opens the songs view and targets the preview",()=>{
  const VoiceCapture=()=>null, SongConverter=()=>null;
  let target;
  const view=mountComponent("src/app/page.tsx",{
    modules:{"@/components/VoiceCapture":{default:VoiceCapture},"@/components/SongConverter":{default:SongConverter}},
    globals:{requestAnimationFrame:callback=>callback(),document:{getElementById:id=>({scrollIntoView:()=>{target=id;}})}},
  });
  assert.equal(view.button("Probar mi voz"),undefined);
  view.find(node=>node.type===VoiceCapture).props.onModel(trained);
  view.button("Probar mi voz").props.onClick();
  assert.equal(target,"voice-audition");
  const container=view.find(node=>node.type==="div" && node.props.children?.type===SongConverter);
  assert.equal(container.props.hidden,false);
  assert.equal(typeof view.find(node=>node.type===VoiceCapture).props.onPreview,"function");
});

test("preview is first and uses the selected song, language and start",async()=>{
  let sent, updated;
  const view=mountComponent("src/components/SongConverter.tsx",{
    props:{model:trained,onModel:model=>{updated=model;}},modules:{"./AudioCompare":{default:AudioCompare}},
    request:(path,options)=>{
      if(options?.method==="POST") {sent={path,body:JSON.parse(options.body)};return Promise.resolve({...trained,preview:{status:"processing"}});}
      return Promise.resolve({songs,uploads:[]});
    },
  });
  await flush();
  const section=view.find(node=>node.props.id==="voice-audition");
  assert.ok(view.nodes().indexOf(section) < view.nodes().findIndex(node=>node.props.className==="library-heading"));
  view.find(node=>node.props["aria-label"]==="Canción para la prueba").props.onChange({target:{value:"song2"}});
  view.find(node=>node.props["aria-label"]==="Inicio de la prueba en segundos").props.onChange({target:{value:"45"}});
  view.find(node=>node.props["aria-label"]==="Tono de la prueba").props.onChange({target:{value:"-12"}});
  assert.equal(view.button("Generar prueba").props.disabled,false);
  await view.button("Generar prueba").props.onClick();
  assert.deepEqual(sent,{path:"/studio/luis_felipe/preview",body:{song_id:"song2",start_seconds:45,language:"es",pitch_shift:-12}});
  assert.equal(updated.preview.status,"processing");
});

test("saved preview pitch survives reload and enables reconversion at a different tone",async()=>{
  const view=mountComponent("src/components/SongConverter.tsx",{
    props:{model:{...trained,approved:true,preview:{status:"completed",pitch_shift:-12}},onModel:()=>{}},
    modules:{"./AudioCompare":{default:AudioCompare}},
    request:()=>Promise.resolve({songs:songs.map(song=>({...song,status:"completed",proof:{conversion:{pitch_shift:0}}})),uploads:[]}),
  });
  await flush();
  assert.equal(view.find(node=>node.props["aria-label"]==="Tono de la prueba").props.value,-12);
  assert.equal(view.find(node=>node.props.id==="pitch").props.value,-12);
  assert.equal(view.button("Convertir pendientes").props.disabled,false);
  view.find(node=>node.props["aria-label"]==="Tono de la prueba").props.onChange({target:{value:"0"}});
  assert.equal(view.button("Convertir pendientes").props.disabled,true);
});

test("preview stays blocked for missing language, stale model or processing",async()=>{
  const view=mountComponent("src/components/SongConverter.tsx",{
    props:{model:trained,onModel:()=>{}},modules:{"./AudioCompare":{default:AudioCompare}},
    request:()=>Promise.resolve({songs:[{...songs[0],language:undefined}],uploads:[]}),
  });
  await flush();
  assert.equal(view.button("Generar prueba").props.disabled,true);
  assert.match(view.text(),/Falta el idioma/);
  view.setProps({model:{...trained,stale:true}});
  assert.equal(view.button("Generar prueba").props.disabled,true);
  view.setProps({model:{...trained,preview:{status:"processing"}}});
  assert.equal(view.button("Generando...").props.disabled,true);
  assert.match(view.text(),/Separando la voz/);
});
