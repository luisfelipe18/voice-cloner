const test = require("node:test");
const assert = require("node:assert/strict");
const {mountComponent} = require("./component-harness.cjs");
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const props={model:{status:"trained",approved:true},ready:true,disabled:false,onModel:()=>{},onBusy:()=>{}};
function mount(request,extra={}) {
  return mountComponent("src/components/ModelTransfer.tsx",{props:{...props,...extra.props},request,
    globals:{FormData:class {append() {}},...extra.globals}});
}

test("export uses a download URL, not a multi-gigabyte browser blob",async()=>{
  const calls=[], downloads=[];
  const view=mount((path,options)=>{
    calls.push([path,JSON.parse(options.body)]);
    return Promise.resolve({download_url:"/api/studio/user/bundles/download/model-id",filename:"voice-model.zip"});
  },{globals:{document:{createElement:()=>({click(){downloads.push(this.href);},remove(){}}),body:{appendChild(){}}}}});
  await view.button("Exportar modelo").props.onClick();
  assert.deepEqual(calls,[["/studio/luis_felipe/bundles/export",{kind:"model"}]]);
  assert.deepEqual(downloads,["/api/studio/user/bundles/download/model-id"]);
  assert.match(view.text(),/Paquete privado: contiene tu voz/);
});

test("import requires trusted origin, reports errors and forwards the new inference model",async()=>{
  let updated,mode="error";
  const view=mount((path,options)=>{
    assert.equal(path,"/studio/luis_felipe/bundles/import");
    return mode==="error" ? Promise.reject(new Error("Checksum incorrecto")) : Promise.resolve({status:"trained",approved:false,inference_only:true});
  },{props:{onModel:model=>{updated=model;}}});
  const input=()=>view.find(node=>node.props["aria-label"]==="Importar modelo exportado");
  assert.equal(input().props.disabled,true);
  view.find(node=>node.type==="input" && node.props.type==="checkbox").props.onChange({target:{checked:true}});
  input().props.onChange({target:{files:[{name:"voice-model.zip",size:120}],value:""}});
  await flush();
  assert.deepEqual(view.alerts(),["Checksum incorrecto"]);
  mode="ok";
  input().props.onChange({target:{files:[{name:"voice-model.zip",size:120}],value:""}});
  await flush();
  assert.equal(updated.inference_only,true);
  assert.equal(updated.approved,false);
  assert.equal(input().props.disabled,true);
});

test("training exports require data and full training models, while inference export stays usable",()=>{
  const view=mount(()=>{}, {props:{ready:false,model:{status:"trained",approved:false,inference_only:true}}});
  assert.equal(view.button("Paquete privado para Colab").props.disabled,true);
  assert.equal(view.button("Exportar modelo").props.disabled,false);
  view.setProps({model:{status:"training",approved:false},ready:true});
  assert.equal(view.button("Exportar modelo").props.disabled,true);
  assert.equal(view.button("Paquete privado para Colab").props.disabled,true);
});
