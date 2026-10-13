const test=require("node:test");
const assert=require("node:assert/strict");
const {mountComponent}=require("./component-harness.cjs");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const existing={song_id:"existing",name:"existing.mp3",language:"es",status:"pending"};
const saved={song_id:"new",name:"new.mp3",language:"en",status:"pending"};
class TestFormData {
  constructor(){this.values=new Map();}
  append(name,value){this.values.set(name,value);}
  get(name){return this.values.get(name);}
}
function mount(request) {
  return mountComponent("src/components/SongConverter.tsx",{
    request,props:{model:{status:"missing",approved:false},onModel:()=>{}},
    modules:{"./AudioCompare":{default:()=>null}},globals:{FormData:TestFormData},
  });
}
function selectFile(view,file={name:"new.mp3",size:1000}) {
  const target={files:[file],value:file.name};
  view.find(node=>node.type==="input" && node.props["aria-label"]==="Agregar cancion").props.onChange({target});
  assert.equal(target.value,"");
}

test("upload uses canonical endpoint and appears immediately despite an old library poll",async()=>{
  let posts=0, gets=0, resolvePoll;
  const stale=new Promise(resolve=>{resolvePoll=resolve;});
  const view=mount((path,options)=>{
    if(options?.method==="POST") {
      posts++;
      assert.equal(path,"/songs");
      assert.equal(options.body.get("language"),"en");
      assert.equal(options.body.get("song").name,"new.mp3");
      return Promise.resolve(saved);
    }
    return ++gets===2 ? stale : Promise.resolve({songs:[existing],uploads:[]});
  });
  await flush();view.poll();
  view.find(node=>node.props["aria-label"]==="Idioma de la cancion nueva").props.onChange({target:{value:"en"}});
  selectFile(view);await flush();
  assert.equal(posts,1);
  assert.match(view.text(),/Canción añadida: new\.mp3/);
  resolvePoll({songs:[existing],uploads:[]});await flush();
  const compare=view.find(node=>typeof node.type==="function" && node.props.song);
  assert.equal(compare.props.song.song_id,"new");
});

test("successful library polling does not erase an upload failure",async()=>{
  const view=mount((path,options)=>options?.method==="POST"
    ? Promise.reject(new Error("No se pudo subir el archivo.")) : Promise.resolve({songs:[existing],uploads:[]}));
  await flush();selectFile(view);await flush();
  view.poll();await flush();
  assert.deepEqual(view.alerts(),["No se pudo subir el archivo."]);
  assert.doesNotMatch(view.text(),/Canción añadida/);
  assert.equal(view.find(node=>node.props["aria-label"]==="Agregar cancion").props.disabled,false);
});

test("empty and oversized files are rejected before starting a request",async()=>{
  let posts=0;
  const view=mount((path,options)=>{if(options?.method==="POST") posts++;return Promise.resolve({songs:[],uploads:[]});});
  await flush();selectFile(view,{name:"empty.mp3",size:0});
  assert.deepEqual(view.alerts(),["El archivo está vacío."]);
  selectFile(view,{name:"large.wav",size:100*1024*1024+1});
  assert.deepEqual(view.alerts(),["La canción supera el límite de 100 MB."]);
  assert.equal(posts,0);
});
