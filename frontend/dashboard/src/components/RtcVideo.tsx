// The WebRTC picture of the selected person's phone: a <video> that plays the viewer's MediaStream inside the
// Live page's camera frame. It reports the stream's pixel size (videoWidth x videoHeight) so the frame keeps
// the picture's aspect ratio, as it does for a JPEG frame. Muted, inline, autoplay: there is no audio track.
import { useEffect, useRef } from "preact/hooks";
import "../styles/rtc.css";

export interface RtcVideoProps {
  stream: MediaStream;
  onSize?: (size: { w: number; h: number }) => void;
}

export function RtcVideo({ stream, onSize }: RtcVideoProps) {
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
      const p = v.play?.();
      if (p && typeof p.catch === "function") p.catch(() => {});
    } catch {
      /* autoplay refused: the picture waits for a click */
    }
  }, [stream]);
  const report = () => {
    const v = ref.current;
    if (v && v.videoWidth > 0 && v.videoHeight > 0) onSize?.({ w: v.videoWidth, h: v.videoHeight });
  };
  return (
    <video
      ref={ref}
      class="camera__video"
      autoplay
      muted
      playsInline
      onLoadedMetadata={report}
      onResize={report}
      data-testid="rtc-video"
    />
  );
}
