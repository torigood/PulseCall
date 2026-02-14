"use client";

import { useState, useRef, useCallback } from "react";

export default function Home() {
  const [isRecording, setIsRecording] = useState(false);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

      const mimeType = MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "audio/mp4";

      const mediaRecorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) {
          chunksRef.current.push(e.data);
        }
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const url = URL.createObjectURL(blob);
        setAudioUrl(url);
        stream.getTracks().forEach((track) => track.stop());
      };

      mediaRecorder.start();
      setIsRecording(true);
      setAudioUrl(null);
      setDuration(0);

      timerRef.current = setInterval(() => {
        setDuration((prev) => prev + 1);
      }, 1000);
    } catch (error) {
      alert("Requrie to access microphone.");
    }
  }, []);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
  }, [isRecording]);

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
      .toString()
      .padStart(2, "0");
    const s = (seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950">
      <div className="flex flex-col items-center gap-8">
        <h1 className="text-3xl font-bold text-white">PulseCall</h1>

        <div className="text-5xl font-mono text-zinc-400">
          {formatTime(duration)}
        </div>

        <button
          onClick={isRecording ? stopRecording : startRecording}
          className={`flex h-24 w-24 items-center justify-center rounded-full transition-all duration-200 ${
            isRecording
              ? "bg-red-500 hover:bg-red-600 animate-pulse"
              : "bg-white hover:bg-zinc-200"
          }`}
        >
          {isRecording ? (
            <div className="h-8 w-8 rounded-sm bg-white" />
          ) : (
            <svg
              className="h-10 w-10 text-zinc-900"
              fill="currentColor"
              viewBox="0 0 24 24"
            >
              <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z" />
              <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" />
            </svg>
          )}
        </button>

        <p className="text-sm text-zinc-500">
          {isRecording ? "Recording... Stop recording" : "Press the button to start recording your voice."}
        </p>

        {audioUrl && (
          <div className="flex flex-col items-center gap-4">
            <audio controls src={audioUrl} className="w-72" />
            <a
              href={audioUrl}
              download="recording"
              className="text-sm text-blue-400 hover:text-blue-300 underline"
            >
              Download
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
