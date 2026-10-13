"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Pause, Play, Volume2 } from "lucide-react";
import { Song } from "@/lib/api";

export default function AudioCompare({ song }: { song: Song }) {
  const [version, setVersion] = useState("original");
  const [isolated, setIsolated] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [error, setError] = useState("");
  const [peaks, setPeaks] = useState<number[]>([]);
  const [volume, setVolume] = useState(0.8);
  const audio = useRef<HTMLAudioElement>(null);
  const resume = useRef({time: 0, playing: false});
  const completed = song.status === "completed";
  const source = isolated ? `/api/songs/${song.song_id}/vocals/${version === "original" ? "original" : "converted"}` :
    version === "original" ? `/api/songs/${song.song_id}/original` : song.download_url;

  useEffect(() => {
    setVersion("original"); setIsolated(false); setTime(0); setPlaying(false); setError("");
    resume.current = {time: 0, playing: false};
    const controller = new AbortController();
    fetch(`/api/songs/${song.song_id}/waveform`, {signal: controller.signal})
      .then(r => r.ok ? r.json() : Promise.reject()).then(data => setPeaks(data.peaks)).catch(() => setPeaks([]));
    return () => controller.abort();
  }, [song.song_id]);

  function switchSource(nextVersion: string, nextIsolated: boolean) {
    resume.current = {time: audio.current?.currentTime || 0, playing: !audio.current?.paused};
    setVersion(nextVersion); setIsolated(nextIsolated); setError("");
  }
  const format = (seconds: number) => `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
  return <section className="comparison">
    <div className="player-heading"><div><span className="eyebrow">Escuchar</span><h2>{song.name.replace(/\.\w+$/, "").replace(/-/g, " ")}</h2></div>
      {completed && <a className="icon-button" href={song.download_url} download title="Descargar MP3" aria-label="Descargar MP3"><Download size={19} /></a>}</div>
    <div className="compare-options"><div className="segmented" aria-label="Version de audio">
      <button aria-pressed={version === "original"} onClick={() => switchSource("original", isolated)}>Original</button>
      <button disabled={!completed} aria-pressed={version === "converted"} onClick={() => switchSource("converted", isolated)}>Mi voz</button>
    </div><label className="check-label"><input type="checkbox" checked={isolated} disabled={!completed} onChange={e => switchSource(version, e.target.checked)} />Solo voz</label></div>
    <div className="waveform" aria-hidden="true">{peaks.map((peak, index) => <i key={index} className={index / peaks.length < time / Math.max(duration,1) ? "heard" : ""} style={{height: `${Math.max(4, peak * 100)}%`}} />)}</div>
    <div className="playback"><button className="play-button" title={playing ? "Pausar" : "Reproducir"} aria-label={playing ? "Pausar" : "Reproducir"} onClick={() => {
      if (!audio.current) return;
      if (audio.current.paused) audio.current.play().catch(() => setError("No se pudo reproducir el audio.")); else audio.current.pause();
    }}>{playing ? <Pause size={22} fill="currentColor" /> : <Play size={22} fill="currentColor" />}</button>
      <span className="time">{format(time)}</span><input aria-label="Posicion de reproduccion" type="range" min={0} max={duration || 1} step={0.1} value={Math.min(time,duration || 1)} onChange={e => { if (audio.current) { audio.current.currentTime=Number(e.target.value); setTime(Number(e.target.value)); } }} />
      <span className="time">{format(duration)}</span><Volume2 size={17} className="volume-icon" /><input className="volume" aria-label="Volumen" type="range" min={0} max={1} step={0.01} value={volume} onChange={e => {setVolume(Number(e.target.value)); if(audio.current) audio.current.volume=Number(e.target.value);}} />
    </div>
    <audio ref={audio} src={source} preload="metadata" onLoadedMetadata={() => {
      if (!audio.current) return;
      setDuration(audio.current.duration); audio.current.volume=volume;
      audio.current.currentTime = Math.min(resume.current.time, audio.current.duration || 0);
      if(resume.current.playing) audio.current.play().catch(() => setPlaying(false));
    }} onTimeUpdate={() => setTime(audio.current?.currentTime || 0)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onError={() => setError("Audio no disponible.")} />
    {error && <p className="error-text" role="alert">{error}</p>}
    {completed && <div className="output-meta"><span>MP3 · 320 kbps</span><span>Modelo personal · {song.proof?.conversion.model_version}</span></div>}
  </section>;
}
