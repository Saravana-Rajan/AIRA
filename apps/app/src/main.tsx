import { render } from "preact";
import { useEffect, useReducer } from "preact/hooks";
import "./ui/app.css";
import { AiraSocket } from "./net/socket";
import { Screen } from "./ui/Screen";
import { initialScreen, reduceScreen } from "./ui/screenState";

const url = import.meta.env.VITE_AIRA_LIVE_WS ?? "ws://localhost:8080/ws";
const socket = new AiraSocket(url);

// No Firebase Auth in this build: hello carries no idToken; the server maps it to the demo shop.
const HELLO = {
  t: "hello",
  uid: "owner",
  shopId: "murugan_dairy",
  lang: "en",
  verbosity: "short",
  appVersion: "0.1.0",
} as const;

function App() {
  const [state, dispatch] = useReducer(reduceScreen, initialScreen);

  useEffect(() => {
    const off = socket.on((m) => {
      if (import.meta.env.DEV) console.log("[aira]", m);
      dispatch(m);
    });
    socket.connect(HELLO);
    return () => {
      off();
      socket.close();
    };
  }, []);

  return (
    <Screen
      state={state}
      shopName="Murugan Dairy"
      onTalk={() => {
        dispatch({ t: "local", event: "talk" });
        socket.send({ t: "text", text: "hello aira" });
      }}
      onStop={() => {
        dispatch({ t: "local", event: "stop" });
        socket.send({ t: "stop" });
      }}
    />
  );
}

render(<App />, document.getElementById("root")!);
