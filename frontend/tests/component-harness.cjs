const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

// Exercise handlers and effects without a microphone or a real user profile.
function mountComponent(path, {request, props={onModel:()=>{}}, modules={}, globals={}}) {
  const source=ts.transpileModule(fs.readFileSync(path,"utf8"), {
    compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX},
  }).outputText;
  const hooks=[], effects=[], intervals=[];
  let cursor=0, tree, Component;
  const render=()=>{cursor=0;tree=Component(props);};
  const react={
    useState(initial) {
      const index=cursor++;
      if(!(index in hooks)) hooks[index]=initial;
      return [hooks[index], value=>{hooks[index]=typeof value === "function" ? value(hooks[index]) : value;render();}];
    },
    useRef(initial) {
      const index=cursor++;
      if(!(index in hooks)) hooks[index]={current:initial};
      return hooks[index];
    },
    useEffect(effect, deps) {
      const index=cursor++, old=hooks[index];
      if(!old || !deps || deps.some((value,i)=>value!==old[i])) {
        hooks[index]=deps;effects.push(effect);
      }
    },
  };
  const scope={exports:{}, Error, window:{isSecureContext:true}, navigator:{mediaDevices:{getUserMedia:()=>{}}},
    setInterval:callback=>{intervals.push(callback);return intervals.length;},clearInterval:()=>{},
    require:name=>name === "react" ? react : name === "@/lib/api" ? {request}
      : name === "@/lib/recording" ? {} : name in modules ? modules[name]
      : ["./SongPerformance", "./SingingSamples", "./ModelTransfer"].includes(name) ? {default:()=>null} : require(name), ...globals,
  };
  vm.runInNewContext(source,scope);Component=scope.exports.default;render();
  const runEffects=()=>{while(effects.length) effects.shift()();};
  runEffects();
  function nodes(node) {
    if(Array.isArray(node)) return node.flatMap(nodes);
    if(!node || typeof node !== "object") return [];
    return [node,...nodes(node.props?.children)];
  }
  function text(node) {
    if(Array.isArray(node)) return node.map(text).join("");
    if(node == null || typeof node === "boolean") return "";
    return typeof node === "object" ? text(node.props?.children) : String(node);
  }
  return {
    button:label=>nodes(tree).find(node=>node.type === "button" && text(node).trim() === label),
    find:predicate=>nodes(tree).find(predicate),
    nodes:()=>nodes(tree),
    text:()=>text(tree),
    alerts:()=>nodes(tree).filter(node=>node.props?.role === "alert").map(text),
    poll:()=>intervals[0](),
    setProps:next=>{props={...props,...next};render();runEffects();},
  };
}
module.exports={mountComponent};
