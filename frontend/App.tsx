import { useState } from "react";
import { IdentityVerification } from "./components/IdentityVerification";
import { DocumentConfirmResponse } from "./lib/verificationApi";

export default function App() {
  const [lastResult, setLastResult] = useState<DocumentConfirmResponse | null>(null);

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 py-10 px-4 sm:px-6 lg:px-8 font-sans">
      <div className="max-w-4xl mx-auto space-y-8">
        {/* Navigation Bar */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pb-6 border-b border-slate-800">
          <div className="flex items-center space-x-3">
            <span className="text-3xl">🛡️</span>
            <div>
              <h1 className="text-xl font-bold text-white tracking-tight">Suraksha Setu Portal</h1>
              <p className="text-xs text-slate-400">Smart Tourist Safety & Identity Verification</p>
            </div>
          </div>
          <div className="flex items-center space-x-2 text-xs">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <span className="text-slate-300">Backend API: <code className="text-emerald-400 font-mono">http://localhost:8000</code></span>
          </div>
        </div>

        {/* Verification Component */}
        <IdentityVerification
          apiUrl="http://localhost:8000/api/v1/verifications"
          onVerificationComplete={(result) => {
            console.log("Verification callback result:", result);
            setLastResult(result);
          }}
        />

        {/* Diagnostic Debug Drawer */}
        {lastResult && (
          <div className="mt-8 p-4 bg-slate-950 border border-slate-800 rounded-xl text-xs text-slate-400 font-mono space-y-2">
            <div className="flex justify-between items-center text-slate-300 font-bold font-sans">
              <span>Latest Verification Callback Event</span>
              <span className={`px-2 py-0.5 rounded text-[10px] ${lastResult.status === 'VERIFIED' ? 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50' : 'bg-rose-900/60 text-rose-300 border border-rose-700/50'}`}>
                {lastResult.status}
              </span>
            </div>
            <pre className="overflow-x-auto text-[11px] text-emerald-400 p-2 bg-slate-900 rounded">
              {JSON.stringify(lastResult, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
