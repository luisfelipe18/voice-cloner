"use client";

import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Mic, Square, Upload, X, Play, Loader2, RotateCcw } from "lucide-react";
import { request } from "@/lib/api";
import { wavBlob } from "@/lib/recording";
import SingingSamples from "./SingingSamples";
import ModelTransfer from "./ModelTransfer";

type Prompt = {id: string; title: string; text: string; language: string};
export type Sample = {id: string; prompt_id: string; language: string; mode: string; status: string; error?: string; issues: string[];
  guide?: {name: string; start_seconds: number; duration: number; pitch_shift: number}; expected_text?: string;
  quality?: {duration: number; active_seconds: number; clipped_fraction: number}; transcript?: string;
  alignment?: {coverage: number; missing_words: string[]; words: {text: string; start: number; end: number}[]}};
export type Model = {status: string; approved: boolean; stale?: boolean; stage?: string; error?: string; epochs?: number; epoch?: number;
  version?: string; parent_version?: string; training_mode?: string;
  inference_only?: boolean;
  fine_tune?: {available: boolean; changed: boolean; new_samples?: number; reason?: string};
  last_training?: {error?: string; status?: string};
  preview?: {status: string; url?: string; original_url?: string; previous_url?: string; error?: string; song?: string; pitch_shift?: number}};
type Studio = {samples: Sample[]; seconds: Record<string,number>; singing_seconds: number; ready: boolean; missing: string[]; model: Model};
const sampleStatuses: Record<string, string> = {
  queued: "En cola", analyzing: "Analizando", review: "Pendiente de revisión",
  accepted: "Aceptada", excluded: "Excluida", rejected: "Audio no válido", failed: "Análisis fallido",
};

export default function VoiceCapture({onModel, onPreview, onCaptureState}: {onModel: (model: Model) => void; onPreview?: () => void; onCaptureState?: (busy: boolean) => void}) {
  const [studio, setStudio] = useState<Studio | null>(null);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [language, setLanguage] = useState("es");
  const [promptId, setPromptId] = useState("es-01");
  const [mode, setMode] = useState("speak");
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [take, setTake] = useState<{blob: Blob; url: string} | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [singingBusy, setSingingBusy] = useState(false);
  const [transferBusy, setTransferBusy] = useState(false);
  const transferPending = useRef(false);
  const [fineEpochs, setFineEpochs] = useState(50);
  const trainingRequest = useRef(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [manual, setManual] = useState(false);
  const [reviewAction, setReviewAction] = useState<"accept" | "exclude" | null>(null);
  const [reviewError, setReviewError] = useState("");
  const [secure, setSecure] = useState(true);
  const reviewPending = useRef(false);
  const studioRevision = useRef(0);
  const capture = useRef<{context: AudioContext; stream: MediaStream; node: AudioWorkletNode; parts: Float32Array[]; started: number; timeout: ReturnType<typeof setTimeout>} | null>(null);
  const prompt = prompts.find(item => item.id === promptId);
  const sample = studio?.samples.find(item => item.id === selected) || studio?.samples.at(-1);
  const captureLocked = recording || busy || !!take || singingBusy || transferBusy;

  useEffect(() => {onCaptureState?.(captureLocked);}, [captureLocked, onCaptureState]);

  useEffect(() => {
    let active = true;
    setSecure(window.isSecureContext && !!navigator.mediaDevices?.getUserMedia);
    request<{prompts: Prompt[]}>("/studio/prompts").then(data => {if(active) setPrompts(data.prompts);}).catch(err => setError(err.message));
    let latestPoll = 0;
    const poll = () => {
      if(reviewPending.current || trainingRequest.current || transferPending.current) return;
      const revision = studioRevision.current, pollId = ++latestPoll;
      // An older poll must not undo a review that finished while it was in flight.
      const current = () => active && revision === studioRevision.current && pollId === latestPoll && !reviewPending.current && !trainingRequest.current && !transferPending.current;
      request<Studio>("/studio/luis_felipe").then(data => {
        if(current()) {setStudio(data); onModel(data.model);}
      }).catch(err => {if(current()) setError(err.message);});
    };
    poll(); const interval = setInterval(poll, 3000);
    return () => {active=false; clearInterval(interval);};
  }, [onModel]);
  useEffect(() => {
    if (!recording) return;
    const interval = setInterval(() => setSeconds(Math.floor((performance.now()-(capture.current?.started || performance.now()))/1000)), 250);
    return () => clearInterval(interval);
  }, [recording]);
  useEffect(() => () => {if(take) URL.revokeObjectURL(take.url);}, [take]);
  useEffect(() => () => {const current=capture.current; if(current) {clearTimeout(current.timeout); current.stream.getTracks().forEach(track=>track.stop()); current.context.close();}}, []);

  async function stop() {
    const current = capture.current; if(!current) return;
    capture.current=null; setBusy(true); setRecording(false);
    clearTimeout(current.timeout);
    try {
      await current.context.resume();
      await new Promise<void>(resolve => {
        const timeout=setTimeout(resolve,3000);
        current.node.port.onmessage = event => {if(event.data.samples) current.parts.push(event.data.samples); if(event.data.flushed) {clearTimeout(timeout);resolve();}};
        current.node.port.postMessage("flush");
      });
      const blob = wavBlob(current.parts, current.context.sampleRate);
      setTake({blob, url: URL.createObjectURL(blob)});
    } catch(err) {setError(err instanceof Error ? err.message : "No se pudo finalizar la toma.");}
    finally {current.stream.getTracks().forEach(track=>track.stop());await current.context.close();setLevel(0);setBusy(false);}
  }
  async function record() {
    setError(""); setTake(null);
    let stream: MediaStream | undefined; let context: AudioContext | undefined;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false}});
      context = new AudioContext(); await context.resume();
      await context.audioWorklet.addModule("/pcm-recorder.js");
      const node = new AudioWorkletNode(context, "pcm-recorder");
      const parts: Float32Array[] = [];
      node.port.onmessage = event => {if(event.data.samples) {const data=event.data.samples as Float32Array; parts.push(data); setLevel(Math.min(1, Math.sqrt(data.reduce((sum,value)=>sum+value*value,0)/data.length)*4));}};
      context.createMediaStreamSource(stream).connect(node); node.connect(context.destination);
      capture.current={context, stream, node, parts, started:performance.now(), timeout:setTimeout(stop,90000)};
      setSeconds(0); setRecording(true);
    } catch(err) {stream?.getTracks().forEach(track=>track.stop()); context?.close(); setError(err instanceof Error ? err.message : "No se pudo abrir el microfono.");}
  }
  async function upload(blob: Blob, filename: string) {
    setBusy(true); setError("");
    try {
      const form = new FormData(); form.append("audio", blob, filename); form.append("prompt_id", promptId); form.append("mode", mode);
      const item=await request<Sample>("/studio/luis_felipe/samples", {method:"POST",body:form});
      addSample(item); setTake(null);
    } catch(err) {setError(err instanceof Error ? err.message : "Error guardando la toma.");}
    finally {setBusy(false);}
  }
  function addSample(item: Sample) {
    studioRevision.current += 1;
    setStudio(current => current ? {...current, samples: [...current.samples.filter(take=>take.id!==item.id), item]} : current);
    setSelected(item.id); setManual(false); setReviewError("");
  }
  async function review(accept: boolean) {
    if(!sample || reviewPending.current) return;
    reviewPending.current = true; studioRevision.current += 1;
    setBusy(true); setReviewAction(accept ? "accept" : "exclude"); setError(""); setReviewError("");
    try {
      const item = await request<Sample>(`/studio/luis_felipe/samples/${sample.id}/review`, {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({accept, manual}),
      });
      setStudio(current => current ? {...current, samples: current.samples.map(take => take.id === item.id ? item : take)} : current);
      setManual(false);
      try {
        const updated = await request<Studio>("/studio/luis_felipe");
        setStudio(updated); onModel(updated.model);
      } catch {
        setReviewError("La decisión se guardó, pero no se pudo actualizar el resumen. Se reintentará automáticamente.");
      }
    } catch(err) {
      setReviewError(err instanceof Error ? err.message : "No se pudo guardar la decisión. Reintenta.");
    } finally {reviewPending.current = false; setReviewAction(null); setBusy(false);}
  }
  async function train() {
    if(trainingRequest.current) return;
    trainingRequest.current=true; studioRevision.current += 1;
    setBusy(true); setError("");
    try {
      const model=await request<Model>("/studio/luis_felipe/train", {method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({mode:hasPersonalModel ? "fine_tune" : "initial",epochs:hasPersonalModel ? fineEpochs : 200})});
      setStudio(current=>current ? {...current,model} : current); onModel(model);
    } catch(err) {setError(err instanceof Error ? err.message : "No se pudo entrenar.");}
    finally {trainingRequest.current=false;setBusy(false);}
  }
  async function restorePrevious() {
    if(trainingRequest.current) return;
    trainingRequest.current=true;
    setBusy(true);setError("");studioRevision.current += 1;
    try {
      const model=await request<Model>("/studio/luis_felipe/train/restore",{method:"POST"});
      setStudio(current=>current ? {...current,model} : current);onModel(model);
    } catch(err) {setError(err instanceof Error ? err.message : "No se pudo recuperar el modelo anterior.");}
    finally {trainingRequest.current=false;setBusy(false);}
  }
  async function retrySample() {
    if(!sample) return;
    try {await request(`/studio/luis_felipe/samples/${sample.id}/retry`,{method:"POST"});setError("");}
    catch(err) {setError(err instanceof Error ? err.message : "Error reintentando el analisis.");}
  }
  async function cancelTraining() {
    try {await request("/studio/luis_felipe/train/cancel",{method:"POST"});}
    catch(err) {setError(err instanceof Error ? err.message : "No se pudo cancelar.");}
  }
  const minutes = (value: number) => `${Math.floor(value/60)}:${Math.floor(value%60).toString().padStart(2,"0")}`;
  const isTraining = studio?.model.status === "training";
  const hasPersonalModel = studio?.model.status === "trained";
  const fineTune = studio?.model.fine_tune;
  const validFineEpochs = Number.isInteger(fineEpochs) && fineEpochs>=10 && fineEpochs<=200;
  const canReview = !!sample && ["review", "accepted", "excluded"].includes(sample.status);

  return <section className="capture-workspace">
    <div className="capture-heading"><div><span className="eyebrow">Perfil nuevo · Luis Felipe</span><h2>Muestras vocales</h2></div><div className="dataset-metrics">
      <span>Español <strong>{minutes(studio?.seconds.es || 0)}</strong></span><span>Inglés <strong>{minutes(studio?.seconds.en || 0)}</strong></span><span>Canto <strong>{minutes(studio?.singing_seconds || 0)}</strong></span>
    </div></div>
    <ModelTransfer model={studio?.model} ready={!!studio?.ready} disabled={captureLocked && !transferBusy}
      onBusy={value=>{transferPending.current=value;setTransferBusy(value);studioRevision.current+=1;}}
      onModel={model=>{setStudio(current=>current ? {...current,model} : current);onModel(model);}}/>
    <div className="dataset-progress"><i style={{width:`${Math.min(100,((studio?.seconds.es || 0)+(studio?.seconds.en || 0))/600*100)}%`}} /></div>
    <div className={`capture-grid ${mode==="sing" ? "capture-singing" : ""}`}><nav className="prompt-navigation" aria-label="Idioma y textos de grabacion">
      <div className="segmented"><button disabled={captureLocked} aria-pressed={language==="es"} onClick={()=>{setLanguage("es");setPromptId("es-01");}}>Español</button><button disabled={captureLocked} aria-pressed={language==="en"} onClick={()=>{setLanguage("en");setPromptId("en-01");}}>English</button></div>
      {mode==="speak" && prompts.filter(item=>item.language===language).map(item=><button className="prompt-link" aria-current={item.id===promptId ? "true" : undefined} disabled={captureLocked} key={item.id} onClick={()=>{setPromptId(item.id);}}><span>{item.id.slice(-2)}</span>{item.title}<span className="take-count">{studio?.samples.filter(s=>s.prompt_id===item.id && s.status==="accepted").length || ""}</span></button>)}
    </nav><div className="capture-main">
      <div className="capture-toolbar"><div className="segmented"><button disabled={captureLocked} aria-pressed={mode==="speak"} onClick={()=>setMode("speak")}>Hablar</button><button disabled={captureLocked} aria-pressed={mode==="sing"} onClick={()=>setMode("sing")}>Cantar</button></div><span className="muted">WAV · PCM · sin compresión</span></div>
      {mode==="sing" ? <SingingSamples language={language} isTraining={!!isTraining || busy || transferBusy} onSample={addSample} onBusy={setSingingBusy}/> : <>
      <h3>{prompt?.title}</h3><p className="recording-text" lang={language}>{prompt?.text || "Cargando texto..."}</p>
      <div className="record-controls">{recording ? <button className="record-stop" onClick={stop}><Square size={17} fill="currentColor"/>Detener <span>{minutes(seconds)}</span></button> : <button className="primary" disabled={!secure || busy || !prompt || isTraining} onClick={record}><Mic size={17}/>Grabar toma</button>}
        <meter min={0} max={1} value={level} aria-label="Nivel del microfono" />
        <label className={`secondary upload-button ${recording || busy || isTraining ? "disabled" : ""}`}><Upload size={16}/>Importar audio<input aria-label="Importar muestra vocal" type="file" accept=".wav,.flac,.webm,.mp3,.m4a,.ogg" disabled={recording || busy || isTraining} onChange={e=>{const file=e.target.files?.[0];if(file)upload(file,file.name);e.target.value="";}} /></label></div>
      {!secure && <p className="capture-note">Micrófono no disponible por HTTP en red. Grabación en localhost o importación de audio.</p>}
      {take && <div className="take-preview"><audio controls src={take.url}/><button className="primary" disabled={busy} onClick={()=>upload(take.blob,"take.wav")}><Check size={16}/>Guardar y analizar</button><button className="icon-button" title="Descartar toma" aria-label="Descartar toma" onClick={()=>setTake(null)}><X size={17}/></button></div>}
      </>}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="sample-review" aria-busy={!!reviewAction}><div className="review-heading"><h3>Revisión de tomas</h3><select aria-label="Toma a revisar" disabled={!!reviewAction} value={sample?.id || ""} onChange={e=>{setSelected(e.target.value);setManual(false);setReviewError("");}}><option value="" disabled>Sin tomas</option>{studio?.samples.map((item,index)=><option key={item.id} value={item.id}>{index+1} · {item.language.toUpperCase()} · {item.mode==="sing" ? "Canto" : "Habla"} · {sampleStatuses[item.status] || item.status}</option>)}</select></div>
        {!sample ? <p className="muted">Sin muestras nuevas.</p> : <><audio controls key={sample.id} src={`/api/studio/luis_felipe/samples/${sample.id}/audio`}/><div className="review-summary"><span>{sampleStatuses[sample.status] || sample.status}</span>{sample.quality && <span>{sample.quality.duration}s · {sample.quality.active_seconds}s de voz activa</span>}{sample.alignment && <span>{Math.round(sample.alignment.coverage*100)}% palabras coincidentes</span>}</div>
          {sample.guide && <p className="guide-provenance">{sample.guide.name} · {minutes(sample.guide.start_seconds)} · {sample.guide.pitch_shift>0 ? "+" : ""}{sample.guide.pitch_shift} st · canto guiado</p>}
          {["queued","analyzing"].includes(sample.status) && <p className="muted"><Loader2 size={15} className="spin"/>Reconociendo palabras y tiempos...</p>}
          {sample.error && <p className="error-text">{sample.error}</p>}{sample.status==="failed" && <button className="secondary" onClick={retrySample}><Play size={15}/>Reintentar análisis</button>}{sample.issues.map(issue=><p key={issue} className="error-text">{issue}</p>)}
          {sample.alignment && <><div className="word-alignment">{sample.alignment.words.map((word,index)=><span key={index} title={`${word.start?.toFixed(2) ?? "?"}–${word.end?.toFixed(2) ?? "?"} s`}>{word.text}<small>{word.start?.toFixed(1) ?? "?"}</small></span>)}</div>
            {sample.alignment.missing_words.length>0 && <p className="missing-words">No reconocidas: {sample.alignment.missing_words.join(", ")}</p>}
            {sample.alignment.coverage<0.6 && sample.status!=="accepted" && <label className="check-label"><input type="checkbox" disabled={busy} checked={manual} onChange={e=>setManual(e.target.checked)}/>Escuché la toma y confirmé el texto aunque el reconocimiento falló.</label>}
            <div className="review-feedback" role="status" aria-live="polite" data-state={sample.status}>
              {reviewAction ? <><Loader2 size={16} className="spin"/>Guardando decisión...</> : sample.status==="accepted" ? <><Check size={16}/>Toma aceptada. Incluida en las muestras de entrenamiento.</> : sample.status==="excluded" ? <><X size={16}/>Toma excluida. No se usará para entrenar.</> : null}
            </div>
            {reviewError && <p className="error-text" role="alert">{reviewError}</p>}
            <div className="review-actions"><button className="primary" disabled={busy || isTraining || !canReview || sample.status==="accepted" || (sample.alignment.coverage<0.6 && !manual)} onClick={()=>review(true)}>{reviewAction==="accept" ? <Loader2 size={16} className="spin"/> : <Check size={16}/>} {reviewAction==="accept" ? "Guardando..." : sample.status==="accepted" ? "Toma aceptada" : "Aceptar toma"}</button><button className="secondary" disabled={busy || isTraining || !canReview || sample.status==="excluded"} onClick={()=>review(false)}>{reviewAction==="exclude" ? <Loader2 size={16} className="spin"/> : <X size={16}/>} {reviewAction==="exclude" ? "Guardando..." : sample.status==="excluded" ? "Toma excluida" : "Excluir toma"}</button></div></>}
        </>}
      </div>
    </div></div>
    <div className="training-band"><div className="training-summary"><h3>Modelo personal</h3>
      <p className="muted">{studio?.model.stale ? "Muestras actualizadas · fine-tuning pendiente" : studio?.model.stage || "Sin entrenar"}{isTraining && ` · época ${studio?.model.epoch || 0}/${studio?.model.epochs}`}</p>
      {hasPersonalModel && <p className="training-origin">Base: {studio?.model.version}{fineTune?.available && ` · ${fineTune.new_samples || 0} tomas nuevas aceptadas`}</p>}
      {hasPersonalModel && fineTune?.available && !fineTune.changed && <p className="muted">Sin cambios en las muestras aceptadas.</p>}
      {hasPersonalModel && !fineTune?.available && fineTune?.reason && <p className="capture-note">{fineTune.reason}</p>}
      {!studio?.model.inference_only && studio?.missing.map(item=><p className="training-requirement" key={item}><CircleAlert size={13}/>{item}</p>)}
      {(studio?.model.error || studio?.model.last_training?.error) && <p className="error-text" role="alert">{studio.model.error || studio.model.last_training?.error}</p>}
    </div><div className="training-actions">{isTraining ? <button className="secondary" onClick={cancelTraining}><Square size={16}/>Cancelar entrenamiento</button>
      : hasPersonalModel && !studio?.model.stale && !studio?.model.approved ? <button className="primary" disabled={!onPreview || captureLocked} onClick={onPreview}><Play size={16}/>Probar mi voz</button>
      : studio?.model.inference_only ? <button className="secondary" disabled={!onPreview || captureLocked} onClick={onPreview}><Play size={16}/>Probar mi voz</button>
      : <>{hasPersonalModel && <label>Épocas adicionales<input aria-label="Épocas de fine-tuning" type="number" min={10} max={200} step={10} value={fineEpochs} disabled={captureLocked} onChange={e=>setFineEpochs(Number(e.target.value))}/></label>}
        <button className="primary" disabled={!studio?.ready || captureLocked || studio?.model.preview?.status==="processing" || (hasPersonalModel && (!fineTune?.available || !fineTune.changed || !validFineEpochs))} onClick={train}><Play size={16}/>{hasPersonalModel ? "Actualizar voz (fine-tuning)" : "Entrenar voz personal"}</button></>}
      {studio?.model.parent_version && !isTraining && <button className="secondary" disabled={captureLocked || studio.model.preview?.status==="processing"} onClick={restorePrevious}><RotateCcw size={16}/>Volver al modelo anterior</button>}
    </div></div>
  </section>;
}
