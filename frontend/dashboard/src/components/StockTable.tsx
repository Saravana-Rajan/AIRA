import { useEffect, useState } from "preact/hooks";
import { collection, onSnapshot } from "firebase/firestore";
import type { InventoryDoc, Product } from "@aira/contracts";
import { db } from "../lib/firebase";
import { toRows } from "../lib/stock";
import { DataTable } from "./DataTable";

/** Realtime stock table for one shop: products joined with inventory. Read-only. */
export function StockTable({ shopId }: { shopId: string }) {
  const [products, setProducts] = useState<Product[]>([]);
  const [inventory, setInventory] = useState<InventoryDoc[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onError = () => setError("Stock is unavailable right now.");
    const u1 = onSnapshot(
      collection(db, `shops/${shopId}/products`),
      (s) => setProducts(s.docs.map((d) => d.data() as Product)),
      onError,
    );
    const u2 = onSnapshot(
      collection(db, `shops/${shopId}/inventory`),
      (s) => setInventory(s.docs.map((d) => d.data() as InventoryDoc)),
      onError,
    );
    return () => {
      u1();
      u2();
    };
  }, [shopId]);

  if (error) return <p role="status">{error}</p>;
  const rows = toRows(products, inventory);

  return (
    <DataTable caption="Live stock, updated in real time" columns={[{ label: "Product" }, { label: "On hand", num: true }, { label: "Status" }]}>
      {rows.length === 0 ? (
        <tr>
          <td colSpan={3} class="empty-cell">No products yet.</td>
        </tr>
      ) : (
        rows.map((r) => (
          <tr key={r.sku}>
            <th scope="row">{r.name}</th>
            <td class="num">
              {r.onHand} {r.unit}
            </td>
            <td>
              <span class={r.low ? "badge badge--low" : "badge badge--ok"}>{r.low ? "Low stock" : "OK"}</span>
            </td>
          </tr>
        ))
      )}
    </DataTable>
  );
}
