"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Headphones, Loader2, Mic, Minus, Music2, Plus, RefreshCw, Square, Upload, X } from "lucide-react";
import { request } from "@/lib/api";
import { startPerformanceCapture, type PerformanceCapture } from "@/lib/performance-recording";
import type { Sample } from "./VoiceCapture";

type Range = {low_hz: number; median_hz: number; high_hz: number};
type Guide = {id: string; song_id: string; name: string; language: string; start_seconds: number;
  duration: number; text: string; word_count: number; range: Range | null; recommended_pitch: number;
  isolated_available: boolean};
type Catalog = {status: string; stage?: string; progress: number; error?: string; warnings: string[];
  guides: Guide[]; songs: {song_id: string; name: string; language: string}[]; personal_range?: Range | null};
type Draft = {blob: Blob; url: string; guide: Guide; pitch: number; kind: string; text: string};
const clock = (value: number) => `${Math.floor(value / 60)}:${Math.floor(value % 60).toString().padStart(2, "0")}`;
const title = (name: string) => name.replace(/\.[^.]+$/, "").replace(/[-_]/g, " ");

export default function SingingSamples({language, isTraining, onSample, onBusy}: {
  language: string; isTraining: boolean; onSample: (sample: Sample) => void; onBusy: (busy: boolean) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [guideId, setGuideId] = useState("");
  const [pitch, setPitch] = useState(0);
  const [kind, setKind] = useState("mix");
  const [text, setText] = useState("");
  const [headphones, setHeadphones] = useState(false);
  const [loop, setLoop] = useState(false);
  const [volume, setVolume] = useState(.45);
  const [secure, setSecure] = useState(true);
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [time, setTime] = useState(-3);
  const [level, setLevel] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [songId, setSongId] = useState("");
  const [start, setStart] = useState(30);
  const [duration, setDuration] = useState(24);
  const capture = useRef<PerformanceCapture | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const mounted = useRef(true);
  const revision = useRef(0);
  const saving = useRef(false);
  const guides = catalog?.guides.filter(item => item.language === language) || [];
  const guide = guides.find(item => item.id === guideId);
  const locked = busy || recording || !!draft;
  const preparing = catalog?.status === "queued" || catalog?.status === "preparing";
  const audioUrl = guide ? `/api/studio/luis_felipe/singing-guides/${guide.id}/audio?pitch=${pitch}&kind=${kind}` : "";

  useEffect(() => {
    mounted.current = true;
    setSecure(window.isSecureContext && !!navigator.mediaDevices?.getUserMedia);
    let latestPoll = 0;
    const poll = () => {
      const version = revision.current, pollId = ++latestPoll;
      request<Catalog>("/studio/luis_felipe/singing-guides").then(data => {
        if (mounted.current && version === revision.current && pollId === latestPoll) setCatalog(data);
      }).catch(err => {if (mounted.current) setError(err.message);});
    };
    poll(); const interval = setInterval(poll, 3000);
    return () => {mounted.current = false; clearInterval(interval); capture.current?.cancel();};
  }, []);
  useEffect(() => {
    if (locked) return;
    const selected = catalog?.guides.find(item => item.id === guideId && item.language === language)
      || catalog?.guides.find(item => item.language === language);
    if (selected && selected.id !== guideId) selectGuide(selected);
    if (!selected) setGuideId("");
  }, [catalog, language, guideId, locked]);
  useEffect(() => {onBusy(locked);}, [locked, onBusy]);
  useEffect(() => () => {if (draft) URL.revokeObjectURL(draft.url);}, [draft]);
  useEffect(() => {if (player.current) player.current.volume = volume;}, [volume, audioUrl]);

  function selectGuide(item: Guide) {
    player.current?.pause(); setGuideId(item.id); setPitch(item.recommended_pitch);
    setKind("mix"); setText(item.text); setError("");
  }
  async function prepare(custom = false) {
    if (busy) return;
    setBusy(true); setError(""); revision.current += 1;
    try {
      const data = await request<Catalog>("/studio/luis_felipe/singing-guides", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(custom ? {song_id: songId, start_seconds: start, duration} : {}),
      });
      if (mounted.current) setCatalog(data);
    } catch (err) {setError(err instanceof Error ? err.message : "No se pudieron preparar las guías.");}
    finally {setBusy(false);}
  }
  async function record() {
    if (!guide || locked || !headphones || isTraining || !secure) return;
    player.current?.pause(); setBusy(true); setError(""); setTime(-3);
    const selected = {guide, pitch, kind, text};
    try {
      const session = await startPerformanceCapture({seconds: guide.duration, guideUrl: audioUrl,
        guideStart: 0, guideVolume: volume, onLevel: setLevel, onTime: setTime});
      capture.current = session;
      if (!mounted.current) {session.cancel(); await session.finished; return;}
      setRecording(true); setBusy(false);
      const blob = await session.finished;
      if (mounted.current) setDraft({...selected, blob, url: URL.createObjectURL(blob)});
    } catch (err) {if (mounted.current) setError(err instanceof Error ? err.message : "No se pudo grabar.");}
    finally {capture.current = null; if (mounted.current) {setRecording(false); setBusy(false); setLevel(0);}}
  }
  async function upload(blob: Blob, filename: string, selected: Omit<Draft, "blob" | "url">) {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError("");
    try {
      const form = new FormData(); form.append("audio", blob, filename);
      form.append("prompt_id", selected.guide.id); form.append("guide_id", selected.guide.id);
      form.append("mode", "sing"); form.append("pitch_shift", String(selected.pitch));
      form.append("guide_kind", selected.kind); form.append("expected_text", selected.text);
      const item = await request<Sample>("/studio/luis_felipe/samples", {method: "POST", body: form});
      if (mounted.current) {onSample(item); setDraft(null);}
    } catch (err) {setError(err instanceof Error ? err.message : "No se pudo guardar la toma.");}
    finally {saving.current = false; setBusy(false);}
  }

  const factor = 2 ** (pitch / 12);
  return <div className="singing-workspace">
    <div className="guide-catalog-heading"><h3>Fragmentos cantados · {guides.length}</h3>
      <button className="secondary" disabled={locked || preparing || isTraining} onClick={()=>prepare()}>
        {preparing ? <Loader2 size={15} className="spin"/> : <RefreshCw size={15}/>}
        {catalog?.guides.length ? "Actualizar selección" : "Preparar fragmentos"}</button></div>
    {preparing && <div className="guide-progress" role="status"><span><Loader2 size={15} className="spin"/>{catalog?.stage} · {catalog?.progress}%</span><progress max={100} value={catalog?.progress}/></div>}
    {catalog?.error && <p className="error-text" role="alert">{catalog.error}</p>}
    <div className="singing-grid"><nav className="guide-navigation" aria-label="Fragmentos cantados">
      {guides.map(item=><button key={item.id} className="guide-link" disabled={locked || isTraining}
        aria-current={guide?.id === item.id ? "true" : undefined} onClick={()=>selectGuide(item)}>
        <Music2 size={16}/><span>{title(item.name)}<small>{clock(item.start_seconds)}–{clock(item.start_seconds + item.duration)} · {item.word_count} palabras distintas</small></span></button>)}
      {!guides.length && <p className="muted">Sin fragmentos en {language === "es" ? "español" : "inglés"}.</p>}
    </nav><div className="guide-detail">{guide && <>
      <h3>{title(guide.name)}</h3><div className="guide-meta"><span>{clock(guide.start_seconds)}–{clock(guide.start_seconds + guide.duration)}</span><span>{guide.duration}s</span>
        {guide.range && <span>Rango estimado · {Math.round(guide.range.low_hz * factor)}–{Math.round(guide.range.high_hz * factor)} Hz</span>}</div>
      <div className="guide-options"><label>Tono <div className="pitch-stepper">
        <button className="icon-button" title="Bajar un semitono" aria-label="Bajar un semitono" disabled={locked || pitch<=-12} onClick={()=>setPitch(pitch-1)}><Minus size={16}/></button>
        <output>{pitch>0 ? "+" : ""}{pitch} st</output><button className="icon-button" title="Subir un semitono" aria-label="Subir un semitono" disabled={locked || pitch>=12} onClick={()=>setPitch(pitch+1)}><Plus size={16}/></button></div></label>
        <label>Pista<select aria-label="Pista guía" disabled={locked} value={kind} onChange={e=>setKind(e.target.value)}><option value="mix">Canción original</option>{guide.isolated_available && <option value="vocal">Voz aislada</option>}</select></label>
        <label>Volumen<input aria-label="Volumen de la guía" type="range" min={0} max={1} step={.05} disabled={locked} value={volume} onChange={e=>setVolume(Number(e.target.value))}/></label>
        <label className="check-label"><input type="checkbox" disabled={locked} checked={loop} onChange={e=>setLoop(e.target.checked)}/>Repetir</label></div>
      {catalog?.personal_range && <p className="muted guide-range">Referencia de tus tomas aceptadas · {Math.round(catalog.personal_range.low_hz)}–{Math.round(catalog.personal_range.high_hz)} Hz</p>}
      <audio key={audioUrl} ref={player} controls loop={loop} src={audioUrl} onPlay={()=>{if(locked) player.current?.pause();}} onError={()=>setError("No se pudo cargar el fragmento. Reintenta o actualiza la selección.")}/>
      <label className="guide-lyrics">Texto del fragmento · reconocimiento aproximado<textarea aria-label="Texto del fragmento" lang={language} disabled={locked} value={text} onChange={e=>setText(e.target.value)} maxLength={3000} rows={3}/></label>
      <label className="check-label"><input aria-label="Uso auriculares" type="checkbox" disabled={locked} checked={headphones} onChange={e=>setHeadphones(e.target.checked)}/><Headphones size={15}/>Uso auriculares · pista guía fuera del micrófono</label>
      <div className="record-controls">{recording ? <button className="record-stop" onClick={()=>capture.current?.stop()}><Square size={16} fill="currentColor"/>Detener <span>{time<0 ? Math.ceil(-time) : `${clock(time)} / ${clock(guide.duration)}`}</span></button>
        : <button className="primary" disabled={locked || !secure || !headphones || isTraining || !text.trim()} onClick={record}>{busy ? <Loader2 size={17} className="spin"/> : <Mic size={17}/>}Grabar fragmento</button>}
        <meter min={0} max={1} value={level} aria-label="Nivel del micrófono cantado"/>
        <label className={`secondary upload-button ${locked || isTraining ? "disabled" : ""}`}><Upload size={16}/>Importar mi canto<input aria-label="Importar canto del fragmento" type="file" accept=".wav,.flac,.webm,.mp3,.m4a,.ogg" disabled={locked || isTraining || !text.trim()} onChange={e=>{const file=e.target.files?.[0];if(file) upload(file,file.name,{guide,pitch,kind,text});e.target.value="";}}/></label></div>
      {recording && <p className="capture-countdown" role="status" aria-live="polite">{time<0 ? `Empieza en ${Math.ceil(-time)}` : "Grabando solo tu micrófono"}</p>}
      {!secure && <p className="capture-note">Micrófono no disponible por HTTP en red. Graba en localhost o importa tu canto sin música.</p>}
      {draft && <div className="take-preview"><audio controls src={draft.url}/><button className="primary" disabled={busy || isTraining} onClick={()=>upload(draft.blob,"sung-take.wav",draft)}><Check size={16}/>Guardar y analizar</button><button className="icon-button" disabled={busy} title="Descartar canto" aria-label="Descartar canto" onClick={()=>setDraft(null)}><X size={17}/></button></div>}
    </>}</div></div>
    {error && <p className="error-text" role="alert">{error}</p>}
    <details className="custom-guide"><summary>Elegir otro fragmento</summary><div className="custom-guide-fields">
      <label>Canción<select aria-label="Canción para nuevo fragmento" disabled={locked || preparing} value={songId} onChange={e=>setSongId(e.target.value)}><option value="">Seleccionar</option>{catalog?.songs.filter(item=>item.language===language).map(item=><option key={item.song_id} value={item.song_id}>{title(item.name)}</option>)}</select></label>
      <label>Inicio (s)<input aria-label="Inicio del fragmento" type="number" min={0} max={3600} step={1} disabled={locked || preparing} value={start} onChange={e=>setStart(Number(e.target.value))}/></label>
      <label>Duración (s)<input aria-label="Duración del fragmento" type="number" min={12} max={30} step={1} disabled={locked || preparing} value={duration} onChange={e=>setDuration(Number(e.target.value))}/></label>
      <button className="secondary" disabled={locked || preparing || !songId || isTraining || !Number.isFinite(start) || start<0 || start>3600 || duration<12 || duration>30} onClick={()=>prepare(true)}><Plus size={16}/>Añadir fragmento</button></div></details>
    {!!catalog?.warnings.length && <details className="guide-warnings"><summary>Avisos · {catalog.warnings.length}</summary>{catalog.warnings.map((warning,index)=><p className="capture-note" key={index}>{warning}</p>)}</details>}
  </div>;
}
