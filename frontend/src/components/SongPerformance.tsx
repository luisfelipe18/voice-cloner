"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Download, Headphones, Loader2, Mic, Music2, Play, RefreshCw, Save, Square, Upload, X } from "lucide-react";
import { request, Song } from "@/lib/api";
import { PerformanceCapture, startPerformanceCapture } from "@/lib/performance-recording";
import type { Model } from "./VoiceCapture";

type Word = { index: number; text: string; status: string; start: number; end: number; estimated: boolean; correction_id?: string };
type Take = { id: string; mode: string; status: string; duration: number; song_start: number; audio_url: string;
  error?: string; transcript?: string; words: Word[]; stale: boolean; parent_id?: string; word_index?: number;
  approved: boolean; clipped_fraction: number };
type Session = { lyrics: string; language: string; song_duration?: number; takes: Take[]; selected: Record<string, string>;
  backing: { status: string; url?: string; error?: string; kind?: string };
  exports: { id: string; status: string; url?: string; vocal_url?: string; error?: string; take_id: string;
    progress?: number; stage?: string; conversion?: { personal_model: boolean; model_version: string } }[] };
const labels: Record<string, string> = { queued: "En cola", analyzing: "Reconociendo", review: "Revisión disponible",
  failed: "Falló el reconocimiento", matched: "Reconocida", missing: "No reconocida", uncertain: "Tiempo dudoso",
  corrected: "Repetición aceptada", confirmed: "Confirmada al escuchar" };
const minutes = (seconds: number) => `${Math.floor(Math.max(0, seconds) / 60)}:${String(Math.floor(Math.max(0, seconds) % 60)).padStart(2, "0")}`;

export default function SongPerformance({ song, model, onRecording }: { song: Song; model: Model; onRecording: (value: boolean) => void }) {
  const [session, setSession] = useState<Session | null>(null);
  const [lyrics, setLyrics] = useState("");
  const [language, setLanguage] = useState(song.language || "es");
  const [mode, setMode] = useState("read");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [secure, setSecure] = useState(false);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [songStart, setSongStart] = useState(0);
  const [headphones, setHeadphones] = useState(false);
  const [guide, setGuide] = useState("original");
  const [guideVolume, setGuideVolume] = useState(0.4);
  const [wordIndex, setWordIndex] = useState<number | null>(null);
  const [wordStart, setWordStart] = useState(0);
  const [wordEnd, setWordEnd] = useState(1);
  const [intervalChecked, setIntervalChecked] = useState(false);
  const [heardWord, setHeardWord] = useState(false);
  const [manual, setManual] = useState<string | null>(null);
  const [vocalGain, setVocalGain] = useState(0);
  const [backingGain, setBackingGain] = useState(0);
  const [offset, setOffset] = useState(0);
  const [cleanup, setCleanup] = useState(true);
  const [draft, setDraft] = useState<{ blob: Blob; url: string; mode: string; songStart: number;
    parentId?: string; wordIndex?: number; start: number; end: number } | null>(null);
  const capture = useRef<PerformanceCapture | null>(null);
  const mounted = useRef(true);
  const revision = useRef(0);
  const pending = useRef(false);
  const audio = useRef<HTMLAudioElement>(null);
  const playbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const take = session?.takes.find(item => item.id === session.selected[mode]);
  const word = take?.words.find(item => item.index === wordIndex);
  const corrections = session?.takes.filter(item => item.parent_id === take?.id && item.word_index === wordIndex) || [];
  const dirty = !!session && (lyrics.trim() !== session.lyrics || language !== session.language);
  const locked = busy || recording;
  const guideUrl = guide === "instrumental" ? session?.backing.url : `/api/songs/${song.song_id}/original`;
  const unresolved = take?.words.filter(item => ["missing", "uncertain"].includes(item.status)).length || 0;
  const prefix = `/performances/${song.song_id}`;

  useEffect(() => {
    mounted.current = true; let active = true, initialized = false, latest = 0;
    setSecure(window.isSecureContext && !!navigator.mediaDevices?.getUserMedia);
    const poll = async () => {
      if (pending.current) return;
      const version = revision.current, pollId = ++latest;
      try {
        const data = await request<Session>(prefix);
        if (!active || version !== revision.current || pollId !== latest || pending.current) return;
        setSession(data);
        if (!initialized) { setLyrics(data.lyrics); setLanguage(data.lyrics ? data.language : song.language || "es"); initialized = true; }
      } catch (err) { if (active) setError(err instanceof Error ? err.message : "No se pudo cargar la canción."); }
    };
    poll(); const timer = setInterval(poll, 3000);
    return () => { active = false; mounted.current = false; clearInterval(timer); capture.current?.cancel();
      if (playbackTimer.current) clearTimeout(playbackTimer.current); onRecording(false); };
  }, [prefix, song.language, onRecording]);
  useEffect(() => () => { if (draft) URL.revokeObjectURL(draft.url); }, [draft]);
  useEffect(() => { setWordIndex(null); setHeardWord(false); setManual(null); }, [take?.id]);
  useEffect(() => { onRecording(locked || !!draft); }, [locked, draft, onRecording]);

  async function action(path: string, options?: RequestInit) {
    pending.current = true; revision.current++; setBusy(true); setError(""); setNotice("");
    try {
      const data = await request<Session>(`${prefix}${path}`, options);
      if (mounted.current) { revision.current++; setSession(data); }
      return true;
    } catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : "No se pudo guardar."); return false; }
    finally { pending.current = false; if (mounted.current) setBusy(false); }
  }
  const json = (method: string, body: unknown) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  async function saveLyrics() {
    if (await action("/lyrics", json("PUT", { lyrics, language }))) setNotice("Letra guardada.");
  }
  async function importLyrics(file?: File) {
    if (!file) return;
    try {
      if (file.size > 150000) throw new Error("La letra supera el límite de 150 KB.");
      const text = await file.text();
      if (text.length > 30000) throw new Error("La letra supera el límite de 30.000 caracteres.");
      setLyrics(text); setNotice("");
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo leer la letra."); }
  }
  function chooseWord(item: Word) {
    audio.current?.pause(); if (playbackTimer.current) clearTimeout(playbackTimer.current);
    setWordIndex(item.index); setWordStart(item.start); setWordEnd(item.end);
    setIntervalChecked(false); setHeardWord(false); setManual(null); setDraft(null); setError("");
  }
  async function listenWord() {
    if (!audio.current || !take || wordEnd <= wordStart) return;
    try {
      audio.current.currentTime = wordStart;
      await audio.current.play(); setHeardWord(true);
      if (playbackTimer.current) clearTimeout(playbackTimer.current);
      playbackTimer.current = setTimeout(() => audio.current?.pause(), (wordEnd - wordStart) * 1000);
    } catch { setError("No se pudo reproducir la palabra."); }
  }
  async function record(single = false) {
    if (!session) return;
    if (single && (!take || !word)) return;
    setBusy(true); setError(""); setDraft(null); audio.current?.pause();
    const parent = single ? take : undefined;
    const start = mode === "sing" ? single ? take!.song_start + wordStart : songStart : 0;
    const duration = single ? mode === "sing" ? wordEnd - wordStart : 6 :
      mode === "sing" ? Math.min(900, (session.song_duration || 300) - start) : 900;
    if (duration < 0.15 || start < 0) { setError("Revisa el inicio y el intervalo de grabación."); setBusy(false); return; }
    try {
      const recorder = await startPerformanceCapture({ seconds: duration,
        guideUrl: mode === "sing" ? guideUrl : undefined, guideStart: start, guideVolume,
        onLevel: setLevel, onTime: setSeconds });
      if (!mounted.current) { recorder.cancel(); await recorder.finished; return; }
      capture.current = recorder; setRecording(true); setBusy(false);
      const blob = await recorder.finished;
      if (mounted.current) setDraft({ blob, url: URL.createObjectURL(blob), mode, songStart: start,
        parentId: parent?.id, wordIndex: single ? word?.index : undefined, start: wordStart, end: wordEnd });
    } catch (err) { if (mounted.current) setError(err instanceof Error ? err.message : "No se pudo grabar."); }
    finally { capture.current = null; if (mounted.current) { setRecording(false); setBusy(false); } }
  }
  async function uploadTake(blob: Blob, filename: string, single = false) {
    if (!blob.size || blob.size > 100 * 1024 * 1024) { setError("La toma debe contener audio y pesar menos de 100 MB."); return; }
    const form = new FormData(); form.append("audio", blob, filename);
    form.append("mode", draft?.mode || mode); form.append("song_start", String(draft?.songStart ?? songStart));
    const parentId = draft?.parentId || (single ? take?.id : undefined);
    if (parentId) {
      form.append("parent_id", parentId); form.append("word_index", String(draft?.wordIndex ?? wordIndex));
      form.append("target_start", String(draft?.start ?? wordStart)); form.append("target_end", String(draft?.end ?? wordEnd));
    }
    if (await action("/takes", { method: "POST", body: form })) { setDraft(null); setNotice(parentId ? "Repetición guardada. Reconocimiento en curso." : "Toma guardada. Reconocimiento en curso."); }
  }
  const canRecord = !!session?.lyrics && !dirty && !locked && secure &&
    (mode === "read" || (headphones && !!guideUrl));
  const canRepeat = !!take && !take.stale && !!word && canRecord &&
    (mode === "read" || (intervalChecked && wordEnd > wordStart && wordEnd <= take.duration));
  const mixTake = session?.takes.find(item => item.id === session.selected.sing);
  const mixMissing = mixTake?.words.filter(item => ["missing", "uncertain"].includes(item.status)).length || 0;
  const latestExport = session?.exports.at(-1);
  const modelReady = model.status === "trained" && model.approved && !model.stale;

  return <section className="performance-workspace" aria-labelledby="performance-title">
    <div className="review-heading"><h2 id="performance-title">{song.name.replace(/\.\w+$/, "").replace(/-/g, " ")}</h2><span className="muted">Mi interpretación</span></div>
    {error && <p className="error-text" role="alert">{error}</p>}{notice && <p className="upload-feedback" role="status"><Check size={16}/>{notice}</p>}
    <div className="performance-columns"><div className="lyrics-editor">
      <div className="review-heading"><h3>Letra</h3><select aria-label="Idioma de la letra" value={language} disabled={locked} onChange={event => setLanguage(event.target.value)}><option value="es">Español</option><option value="en">English</option></select></div>
      <textarea aria-label="Letra de la canción" lang={language} value={lyrics} maxLength={30000} disabled={locked} onChange={event => setLyrics(event.target.value)}/>
      <div className="performance-actions"><button className="primary" disabled={locked || !lyrics.trim() || (!dirty && !!session?.lyrics)} onClick={saveLyrics}><Save size={16}/>Guardar letra</button><label className={`secondary upload-button ${locked ? "disabled" : ""}`}><Upload size={16}/>Importar letra<input aria-label="Importar letra" type="file" accept=".txt,text/plain" disabled={locked} onChange={event => { importLyrics(event.target.files?.[0]); event.target.value = ""; }}/></label><span className="muted">{dirty ? "Cambios sin guardar" : session?.lyrics ? "Guardada" : "Sin letra"}</span></div>
    </div><div className="performance-capture">
      <div className="capture-toolbar"><div className="segmented" aria-label="Tipo de interpretación"><button aria-pressed={mode === "read"} disabled={locked || !!draft} onClick={() => { setMode("read"); setWordIndex(null); }}>Leer letra</button><button aria-pressed={mode === "sing"} disabled={locked || !!draft} onClick={() => { setMode("sing"); setWordIndex(null); }}>Cantar letra</button></div><span className="muted">{mode === "read" ? "Lectura" : "Canto"}</span></div>
      {mode === "sing" && <><div className="performance-fields"><label>Referencia<select aria-label="Referencia musical" disabled={locked} value={guide} onChange={event => setGuide(event.target.value)}><option value="original">Canción original</option><option value="instrumental" disabled={session?.backing.status !== "ready"}>Instrumental</option></select></label><label>Inicio (s)<input aria-label="Inicio del canto" type="number" min={0} max={session?.song_duration || 1200} step={0.1} disabled={locked} value={songStart} onChange={event => setSongStart(Number(event.target.value))}/></label><label>Referencia<input aria-label="Volumen de referencia" type="range" min={0} max={1} step={0.05} disabled={locked} value={guideVolume} onChange={event => setGuideVolume(Number(event.target.value))}/></label></div><label className="check-label"><input type="checkbox" checked={headphones} disabled={locked} onChange={event => setHeadphones(event.target.checked)}/><Headphones size={15}/>Auriculares conectados</label></>}
      <div className="record-controls">{recording ? <button className="record-stop" onClick={() => capture.current?.stop()}><Square size={16}/>{seconds < 0 ? `Preparación ${Math.ceil(-seconds)}` : `Detener ${minutes(seconds)}`}</button> : <button className="primary" disabled={!canRecord || !!draft} onClick={() => record()}>{busy ? <Loader2 className="spin" size={16}/> : <Mic size={16}/>}Grabar {mode === "read" ? "lectura" : "canto"}</button>}<meter min={0} max={1} value={level} aria-label="Nivel de mi interpretación"/>
        <label className={`secondary upload-button ${locked || dirty || !session?.lyrics || !!draft ? "disabled" : ""}`}><Upload size={16}/>Importar toma<input aria-label="Importar interpretación" type="file" accept=".wav,.flac,.mp3,.m4a,.ogg,.webm" disabled={locked || dirty || !session?.lyrics || !!draft} onChange={event => { const file = event.target.files?.[0]; if (file) uploadTake(file, file.name); event.target.value = ""; }}/></label></div>
      {!secure && <p className="capture-note" role="status">Micrófono bloqueado por HTTP en red. Disponible en localhost o HTTPS. Importación de audio disponible.</p>}
      {draft && <div className="take-preview"><audio controls src={draft.url}/><button className="primary" disabled={locked} onClick={() => uploadTake(draft.blob, "interpretation.wav")}><Check size={16}/>Guardar {draft.parentId ? "palabra" : "toma"}</button><button className="icon-button" title="Descartar grabación" aria-label="Descartar grabación" disabled={locked} onClick={() => setDraft(null)}><X size={16}/></button></div>}
      <div className="sample-review"><div className="review-heading"><h3>Revisión de {mode === "read" ? "lectura" : "canto"}</h3><select aria-label="Interpretación a revisar" disabled={locked || !!draft} value={take?.id || ""} onChange={event => action("/selection", json("PATCH", { take_id: event.target.value }))}><option value="" disabled>Sin tomas</option>{session?.takes.filter(item => item.mode === mode && !item.parent_id).map((item, index) => <option key={item.id} value={item.id}>Toma {index + 1} · {labels[item.status] || item.status}{item.stale ? " · Letra anterior" : ""}</option>)}</select></div>
        {!take ? <p className="muted">Sin toma de {mode === "read" ? "lectura" : "canto"}.</p> : <><audio ref={audio} key={take.id} controls src={take.audio_url}/><div className="review-summary"><span>{labels[take.status]}</span><span>{minutes(take.duration)}</span><span>{unresolved} palabras pendientes</span></div>{take.clipped_fraction > 0.005 && <p className="error-text">La toma contiene saturación.</p>}{take.stale && <p className="capture-note">La letra o el idioma cambiaron después de esta toma.</p>}
          {["queued", "analyzing"].includes(take.status) && <p className="muted" role="status"><Loader2 className="spin" size={15}/>Reconociendo palabras...</p>}
          {take.error && <p className="error-text" role="alert">{take.error}</p>}{take.status === "failed" && <button className="secondary" disabled={locked} onClick={() => action(`/takes/${take.id}/retry`, { method: "POST" })}><RefreshCw size={15}/>Reintentar reconocimiento</button>}
          {take.transcript && <details className="transcript"><summary>Transcripción reconocida</summary><p>{take.transcript}</p></details>}
          <div className="performance-words" aria-label="Palabras de la toma">{take.words.map(item => <button key={item.index} className={`word ${item.status}`} aria-pressed={wordIndex === item.index} title={`${labels[item.status]} · ${item.start.toFixed(2)} s${item.estimated ? " · Tiempo estimado" : ""}`} disabled={locked || !!draft || take.stale} onClick={() => chooseWord(item)}>{item.text}<small>{item.index + 1}</small></button>)}</div>
        </>}
      </div>
    </div></div>
    {word && take && <div className="word-repair" aria-labelledby="word-title"><div className="review-heading"><h3 id="word-title">Palabra {word.index + 1}: {word.text}</h3><span className="muted">{labels[word.status]}</span></div>
      <div className="performance-fields"><label>Inicio en la toma (s)<input aria-label="Inicio de la palabra" type="number" step={0.01} min={0} max={take.duration} value={wordStart} disabled={locked || !!draft} onChange={event => { setWordStart(Number(event.target.value)); setHeardWord(false); setIntervalChecked(false); }}/></label><label>Fin en la toma (s)<input aria-label="Fin de la palabra" type="number" step={0.01} min={0} max={take.duration} value={wordEnd} disabled={locked || !!draft} onChange={event => { setWordEnd(Number(event.target.value)); setHeardWord(false); setIntervalChecked(false); }}/></label><button className="secondary" disabled={locked || wordEnd <= wordStart || wordStart < 0 || wordEnd > take.duration} onClick={listenWord}><Play size={15}/>Escuchar palabra</button><button className="secondary" disabled={locked || !heardWord || take.stale} onClick={() => action(`/takes/${take.id}/words`, json("PATCH", { word_index: word.index, confirmed: word.status !== "confirmed" }))}><Check size={15}/>{word.status === "confirmed" ? "Desmarcar confirmación" : "La palabra está bien"}</button></div>
      {word.estimated && <p className="capture-note">Intervalo estimado; no es una alineación fonética verificada.</p>}
      {mode === "sing" && <label className="check-label"><input type="checkbox" checked={intervalChecked} disabled={locked} onChange={event => setIntervalChecked(event.target.checked)}/>Intervalo de la palabra revisado</label>}
      <div className="performance-actions"><button className="primary" disabled={!canRepeat || !!draft} onClick={() => record(true)}><Mic size={16}/>Repetir solo «{word.text}»</button><label className={`secondary upload-button ${locked || dirty || take.stale || !!draft || (mode === "sing" && !intervalChecked) ? "disabled" : ""}`}><Upload size={16}/>Importar palabra<input aria-label="Importar repetición de palabra" type="file" accept=".wav,.flac,.mp3,.m4a,.ogg,.webm" disabled={locked || dirty || take.stale || !!draft || (mode === "sing" && !intervalChecked)} onChange={event => { const file = event.target.files?.[0]; if (file) uploadTake(file, file.name, true); event.target.value = ""; }}/></label></div>
      {corrections.map(item => <div className="correction-row" key={item.id}><div className="review-summary"><span>{item.approved ? "Repetición aceptada" : labels[item.status]}</span><span>{item.transcript || ""}</span></div><audio controls src={item.audio_url}/>{item.error && <p className="error-text">{item.error}</p>}{item.status === "failed" && <button className="secondary" disabled={locked} onClick={() => action(`/takes/${item.id}/retry`, { method: "POST" })}><RefreshCw size={15}/>Reintentar reconocimiento</button>}
        {!["queued", "analyzing"].includes(item.status) && <><label className="check-label"><input type="checkbox" checked={manual === item.id} disabled={locked} onChange={event => setManual(event.target.checked ? item.id : null)}/>Escuché y confirmé esta repetición</label><button className="secondary" disabled={locked || manual !== item.id || take.stale} onClick={() => action(`/takes/${item.id}/correction`, json("PATCH", { approved: !item.approved, manual: manual === item.id }))}><Check size={15}/>{item.approved ? "Retirar corrección" : "Aceptar repetición"}</button></>}
      </div>)}
    </div>}
    <section className="performance-mix" aria-labelledby="mix-title"><div className="review-heading"><h3 id="mix-title">Modelo, instrumental y mezcla</h3><span className="muted">{session?.backing.status === "ready" ? session.backing.kind === "imported" ? "Instrumental importado" : "Instrumental separado" : "Instrumental pendiente"}</span></div>
      <div className="performance-actions"><button className="secondary" disabled={locked || ["processing", "ready"].includes(session?.backing.status || "")} onClick={() => action("/backing", { method: "POST" })}>{session?.backing.status === "processing" ? <Loader2 className="spin" size={16}/> : <Music2 size={16}/>}Preparar instrumental</button><label className={`secondary upload-button ${locked || session?.backing.status === "processing" ? "disabled" : ""}`}><Upload size={16}/>Importar instrumental<input aria-label="Importar instrumental" type="file" accept=".wav,.flac,.mp3,.m4a,.ogg,.webm" disabled={locked || session?.backing.status === "processing"} onChange={event => { const file = event.target.files?.[0]; if (file) { const form = new FormData(); form.append("audio", file); action("/backing/upload", { method: "POST", body: form }); } event.target.value = ""; }}/></label></div>
      {session?.backing.error && <p className="error-text" role="alert">{session.backing.error}</p>}{session?.backing.url && <audio className="backing-player" controls src={session.backing.url}/>}
      <div className="performance-fields mix-fields"><label>Voz {vocalGain} dB<input aria-label="Nivel de mi voz" type="range" min={-18} max={18} value={vocalGain} onChange={event => setVocalGain(Number(event.target.value))}/></label><label>Música {backingGain} dB<input aria-label="Nivel del instrumental" type="range" min={-18} max={6} value={backingGain} onChange={event => setBackingGain(Number(event.target.value))}/></label><label>Desplazamiento (ms)<input aria-label="Desplazamiento de mi voz" type="number" min={-5000} max={5000} step={10} value={offset} onChange={event => setOffset(Number(event.target.value))}/></label><label className="check-label"><input type="checkbox" checked={cleanup} onChange={event => setCleanup(event.target.checked)}/>Limpieza y compresión</label></div>
      <div className="performance-actions"><button className="primary" disabled={locked || dirty || !modelReady || !mixTake || mixTake.stale || mixTake.status !== "review" || mixMissing > 0 || session?.backing.status !== "ready" || latestExport?.status === "processing"} onClick={() => action("/mix", json("POST", { take_id: mixTake?.id, user_id: "luis_felipe", pitch_shift: 0, vocal_gain_db: vocalGain, backing_gain_db: backingGain, offset_ms: offset, cleanup }))}>{latestExport?.status === "processing" ? <Loader2 className="spin" size={16}/> : <Music2 size={16}/>}Aplicar mi modelo y mezclar</button><span className="muted">{modelReady ? "Modelo personal aprobado" : "Modelo personal pendiente de entrenamiento o aprobación"}</span><span className="muted">{!mixTake ? "Canto pendiente" : mixMissing ? `${mixMissing} palabras del canto pendientes` : "Palabras del canto revisadas"}</span></div>
      {latestExport?.status === "processing" && <div className="performance-progress" role="status"><progress max={100} value={latestExport.progress || 0}/><span>{latestExport.stage || "Aplicando tu modelo personal"}</span></div>}
      {latestExport?.error && <p className="error-text" role="alert">{latestExport.error}</p>}
      {session?.exports.filter(item => item.status === "completed").map((item, index) => <div key={item.id}>
        <div className="performance-export"><span>{item.conversion?.personal_model ? `Modelo aplicado · ${item.conversion.model_version}` : "Mezcla directa anterior · Sin modelo"}</span><audio aria-label={`Mezcla ${index + 1}`} controls src={item.url}/><a className="icon-button" title="Descargar interpretación" aria-label={`Descargar mezcla ${index + 1}`} href={item.url} download><Download size={17}/></a></div>
        {item.vocal_url && <details className="transcript"><summary>Voz de entrada y voz del modelo</summary><div className="audition-pair"><label>Toma sin convertir<audio aria-label="Toma sin convertir" controls src={session.takes.find(take => take.id === item.take_id)?.audio_url}/></label><label>Mi voz procesada con RVC<audio aria-label="Mi voz procesada con RVC" controls src={item.vocal_url}/></label></div></details>}
      </div>)}
    </section>
  </section>;
}
