const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const ts = require("typescript");

const moduleSource = ts.transpileModule(fs.readFileSync("src/lib/recording.ts", "utf8"), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
}).outputText;
const scope = {exports: {}, Blob, ArrayBuffer, DataView, Float32Array};
vm.runInNewContext(moduleSource, scope);

test("recording is mono PCM16 with the microphone's actual sample rate", async () => {
  const blob=scope.exports.wavBlob([new Float32Array([0,0.5]),new Float32Array([-0.5])],48000);
  const bytes=await blob.arrayBuffer(); const view=new DataView(bytes);
  assert.equal(new TextDecoder().decode(bytes.slice(0,4)),"RIFF");
  assert.equal(view.getUint16(22,true),1);
  assert.equal(view.getUint32(24,true),48000);
  assert.equal(view.getUint16(34,true),16);
  assert.equal(view.getUint32(40,true),6);
  assert.equal(view.getInt16(48,true),-16384);
});

test("out-of-range microphone samples clamp instead of wrapping", async () => {
  const view=new DataView(await scope.exports.wavBlob([new Float32Array([2,-2])],44100).arrayBuffer());
  assert.equal(view.getInt16(44,true),32767);
  assert.equal(view.getInt16(46,true),-32768);
});

test("worklet emits full blocks and flushes the last partial block exactly once", () => {
  let Recorder; const messages=[];
  vm.runInNewContext(fs.readFileSync("public/pcm-recorder.js","utf8"), {
    AudioWorkletProcessor: class {constructor(){this.port={postMessage: value=>messages.push(value)};}},
    registerProcessor: (_,implementation)=>{Recorder=implementation;}, Float32Array,
  });
  const recorder=new Recorder();
  for(let index=0;index<34;index++) recorder.process([[new Float32Array(128).fill(0.2)]]);
  recorder.port.onmessage({data:"flush"});
  recorder.port.onmessage({data:"flush"});
  assert.equal(messages.filter(message=>message.samples).reduce((sum,message)=>sum+message.samples.length,0),4352);
  assert.equal(messages.filter(message=>message.flushed).length,2);
});
