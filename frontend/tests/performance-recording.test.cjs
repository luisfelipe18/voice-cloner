const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function recorder(startAt, endAt) {
  let Recorder;
  const messages = [];
  const context = { currentFrame: 0, sampleRate: 10, Float32Array,
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: value => messages.push(value) }; } },
    registerProcessor: (name, cls) => { assert.equal(name, "performance-recorder"); Recorder = cls; },
  };
  vm.runInNewContext(fs.readFileSync("public/performance-recorder.js", "utf8"), context);
  const node = new Recorder();
  node.port.onmessage({ data: { startAt, endAt } });
  return { node, context, messages };
}

test("microphone capture excludes countdown and ends exactly at the guide window", () => {
  const { node, messages } = recorder(.3, .7);
  node.process([[Float32Array.from([0,1,2,3,4,5,6,7,8,9])]]);
  assert.deepEqual(Array.from(messages[0].samples), [3,4,5,6]);
  assert.equal(messages[1].done, true);
  node.port.onmessage({ data: "flush" });
  node.process([[Float32Array.from([10,11])]]);
  assert.equal(messages.length, 2);
});

test("stopping a single-word recording flushes only its captured microphone samples", () => {
  const { node, messages } = recorder(.2, 10);
  node.process([[Float32Array.from([0,1,2,3,4])]]);
  node.port.onmessage({ data: "flush" });
  assert.deepEqual(Array.from(messages[0].samples), [2,3,4]);
  assert.equal(messages[1].done, true);
});
