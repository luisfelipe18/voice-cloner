const test = require("node:test");
const assert = require("node:assert/strict");
const {mountComponent} = require("./component-harness.cjs");
const fixture = status => ({
  samples: [{id:"take1", prompt_id:"es-01", language:"es", mode:"speak", status, issues:[],
    quality:{duration:20, active_seconds:15, clipped_fraction:0},
    alignment:{coverage:1, missing_words:[], words:[]}}],
  seconds:{es:status === "accepted" ? 15 : 0, en:0}, singing_seconds:0,
  ready:false, missing:[], model:{status:"missing", approved:false},
});
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve=yes; reject=no;});
  return {promise, resolve, reject};
}

function mount(request) {
  return mountComponent("src/components/VoiceCapture.tsx",{request});
}

test("accept uses the saved response immediately, then refreshes the summary", async () => {
  const save=deferred(), summary=deferred();
  let gets=0, posts=0;
  const view=mount((path, options)=>{
    if(path.endsWith("/prompts")) return Promise.resolve({prompts:[]});
    if(options?.method === "POST") {
      posts++;
      assert.equal(path,"/studio/luis_felipe/samples/take1/review");
      assert.deepEqual(JSON.parse(options.body),{accept:true,manual:false});
      return save.promise;
    }
    return ++gets === 1 ? Promise.resolve(fixture("review")) : summary.promise;
  });
  await flush();
  const click=view.button("Aceptar toma").props.onClick;
  const pending=click();
  click();
  assert.equal(posts,1);
  assert.equal(view.button("Guardando...").props.disabled,true);
  save.resolve(fixture("accepted").samples[0]);
  await flush();
  assert.match(view.text(),/Aceptada/);
  summary.resolve(fixture("accepted"));
  await pending;
  assert.equal(view.button("Toma aceptada").props.disabled,true);
  assert.match(view.text(),/Toma aceptada\. Incluida en las muestras de entrenamiento\./);
  assert.match(view.text(),/0:15/);
});

test("review errors are visible next to the actions and allow retry", async () => {
  const view=mount((path, options)=>path.endsWith("/prompts") ? Promise.resolve({prompts:[]})
    : options?.method === "POST" ? Promise.reject(new Error("No se pudo guardar la toma."))
    : Promise.resolve(fixture("review")));
  await flush();
  await view.button("Aceptar toma").props.onClick();
  assert.deepEqual(view.alerts(),["No se pudo guardar la toma."]);
  assert.equal(view.button("Aceptar toma").props.disabled,false);
  assert.doesNotMatch(view.text(),/Toma aceptada\./);
});

test("an in-flight poll cannot overwrite a saved review", async () => {
  const stale=deferred(); let gets=0;
  const view=mount((path, options)=>{
    if(path.endsWith("/prompts")) return Promise.resolve({prompts:[]});
    if(options?.method === "POST") return Promise.resolve(fixture("accepted").samples[0]);
    gets++;
    return gets === 2 ? stale.promise : Promise.resolve(fixture(gets === 1 ? "review" : "accepted"));
  });
  await flush(); view.poll();
  await view.button("Aceptar toma").props.onClick();
  stale.resolve(fixture("review"));
  await flush();
  assert.equal(view.button("Toma aceptada").props.disabled,true);
  assert.match(view.text(),/0:15/);
});

test("exclude confirms the saved choice even if summary refresh fails", async () => {
  let gets=0;
  const view=mount((path, options)=>{
    if(path.endsWith("/prompts")) return Promise.resolve({prompts:[]});
    if(options?.method === "POST") return Promise.resolve(fixture("excluded").samples[0]);
    return ++gets === 1 ? Promise.resolve(fixture("accepted")) : Promise.reject(new Error("offline"));
  });
  await flush();
  await view.button("Excluir toma").props.onClick();
  assert.equal(view.button("Toma excluida").props.disabled,true);
  assert.match(view.text(),/Toma excluida\. No se usará para entrenar\./);
  assert.match(view.alerts()[0],/La decisión se guardó/);
  assert.equal(view.button("Aceptar toma").props.disabled,false);
});
