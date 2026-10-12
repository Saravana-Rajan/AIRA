// The WebRTC picture of Sam's phone: a <video> that plays the viewer's MediaStream under the Live page's box
// and fingertip overlay. It reports the stream's pixel size (videoWidth x videoHeight) so the overlay maps the
// 0-1000 coordinates onto the same space as a JPEG frame. Muted, inline, autoplay: there is no audio track.
import { useEffect, useRef } from "preact/hooks";
import "../styles/rtc.css";

export interface RtcVideoProps {
  stream: MediaStream;
  /** Freeze the picture (the Pause camera button). */
  paused?: boolean;
  onSize?: (size: { w: number; h: number }) => void;
}

export function RtcVideo({ stream, paused = false, onSize }: RtcVideoProps) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    try {
      if (v.srcObject !== stream) v.srcObject = stream;
    } catch {
      /* jsdom, or a browser without srcObject: nothing to play */
    }
    try {
      if (paused) v.pause?.();
      else {
        const p = v.play?.();
        if (p && typeof p.catch === "function") p.catch(() => {});
      }
    } catch {
      /* autoplay refused: the picture waits for a click */
    }
  }, [stream, paused]);
  const report = () => {
    const v = ref.current;
    if (v && v.videoWidth > 0 && v.videoHeight > 0) onSize?.({ w: v.videoWidth, h: v.videoHeight });
  };
  return (
    <video
      ref={ref}
      class="camera__img camera__video"
      autoplay
      muted
      playsInline
      onLoadedMetadata={report}
      onResize={report}
      data-testid="rtc-video"
    />
  );
}
