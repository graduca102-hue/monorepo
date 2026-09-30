import { useState, useRef } from 'react';
import toast from 'react-hot-toast';
import { Upload, ArrowRight, ArrowLeft, Loader2, CheckCircle2 } from 'lucide-react';
import { useConnection, useWallet } from '@solana/wallet-adapter-react';
import { LAMPORTS_PER_SOL } from '@solana/web3.js';
import LaunchSuccessModal from './LaunchSuccessModal';
import { useSolanaWallet } from '../hooks/useSolanaWallet';
import { useTokenCreation } from '../hooks/useTokenCreation';
import { useAppStore } from '../stores/useAppStore';
import { env } from '../config/env';
import { registerUserCreatedTokenMint } from '../promoPools/userCreatedMints';
import { parseSolanaError } from '../utils/errorParser';
import {
  buildTokenCreationFeeKinds,
  estimateMinLamportsForTokenCreation,
  type CreationStage,
} from '../services/tokenService';

type FormData = {
  name: string;
  symbol: string;
  image: File | null;
  imagePreview: string;
  decimals: string;
  supply: string;
  description: string;
  website: string;
  twitter: string;
  telegram: string;
  discord: string;
  modifyCreator: boolean;
  creatorName: string;
  creatorWebsite: string;
  dexBoost: boolean;
  revokeFreeze: boolean;
  revokeMint: boolean;
  revokeUpdate: boolean;
};

type Status = 'form' | 'confirming' | 'success';

const STAGE_LABEL: Record<CreationStage, string> = {
  uploading_image: 'Uploading image to IPFS…',
  uploading_metadata: 'Uploading metadata…',
  building_transaction: 'Building transaction…',
  awaiting_signature: 'Approve in your wallet…',
  confirming: 'Confirming on-chain…',
  done: 'Almost done…',
};

function StepIndicator({ step }: { step: number }) {
  return (
    <div className="flex items-center justify-center mb-8">
      {[1, 2, 3].map((n) => (
        <div key={n} className="flex items-center">
          <div
            className={`w-8 h-8 rounded-full flex items-center justify-center font-semibold text-sm transition-all duration-150 ${
              n < step
                ? 'bg-[#86efac] text-[#052e16]'
                : n === step
                ? 'bg-[#86efac] text-[#052e16]'
                : 'bg-[#212225] text-[#696e77] border border-[#272a2d]'
            }`}
          >
            {n < step ? (
              <CheckCircle2 size={14} />
            ) : (
              n
            )}
          </div>
          {n < 3 && (
            <div
              className={`w-16 h-px transition-all duration-150 ${
                n < step ? 'bg-[#86efac]' : 'bg-[#212225]'
              }`}
            />
          )}
        </div>
      ))}
    </div>
  );
}

const TOKEN_NAME_MAX = 32;
const TOKEN_SYMBOL_MAX = 10;
/** Letters and numbers only (no spaces / punctuation). */
const TOKEN_SYMBOL_ALLOWED = /^[A-Za-z0-9]+$/;

const inputCls = (error?: string) =>
  `w-full h-10 rounded-[12px] bg-[#18191b] border ${
    error ? 'border-[#f87171]' : 'border-[#212225]'
  } px-3 text-sm text-[#fafafa] placeholder-[#363a3f] transition-all duration-150 outline-none focus:border-[#696e77] shadow-[0_1px_rgba(0,0,0,0.05)]`;

export default function TokenForm({ onGoToLiquidity }: { onGoToLiquidity: (mint: string) => void }) {
  const { connection } = useConnection();
  const { publicKey, connected } = useWallet();
  const { connect } = useSolanaWallet();
  const { createToken: runCreate, isCreating, currentStage } = useTokenCreation();
  const addUserToken = useAppStore((s) => s.addUserToken);
  const recordTransaction = useAppStore((s) => s.recordTransaction);

  const [step, setStep] = useState(1);
  const [status, setStatus] = useState<Status>('form');
  const [mintAddress, setMintAddress] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<string, string>>>({});
  const fileRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<FormData>({
    name: '',
    symbol: '',
    image: null,
    imagePreview: '',
    decimals: '9',
    supply: '1000000000',
    description: '',
    website: '',
    twitter: '',
    telegram: '',
    discord: '',
    modifyCreator: false,
    creatorName: '',
    creatorWebsite: '',
    dexBoost: true,
    revokeFreeze: true,
    revokeMint: true,
    revokeUpdate: true,
  });

  const set = <K extends keyof FormData>(key: K, value: FormData[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleFile = (file: File | null) => {
    if (!file) return;
    set('image', file);
    setErrors((prev) => {
      const next = { ...prev };
      delete next.image;
      return next;
    });
    const reader = new FileReader();
    reader.onload = (e) => set('imagePreview', e.target?.result as string);
    reader.readAsDataURL(file);
  };

  const validateStep1 = () => {
    const errs: Record<string, string> = {};
    const name = form.name.trim();
    const sym = form.symbol.trim();
    if (!name) errs.name = 'Required';
    else if (name.length > TOKEN_NAME_MAX) errs.name = `Max ${TOKEN_NAME_MAX} characters`;
    if (!sym) errs.symbol = 'Required';
    else if (sym.length > TOKEN_SYMBOL_MAX) errs.symbol = `Max ${TOKEN_SYMBOL_MAX} characters`;
    else if (!TOKEN_SYMBOL_ALLOWED.test(sym)) errs.symbol = 'Letters and numbers only';
    if (!form.image) errs.image = 'Required';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const validateStep2 = () => {
    const errs: Record<string, string> = {};
    if (!form.decimals.trim() || form.decimals === '') errs.decimals = 'Required';
    else {
      const d = Number(form.decimals);
      if (!Number.isInteger(d) || d < 0 || d > 9) errs.decimals = '0–9';
    }
    if (!form.supply || Number(form.supply) <= 0) errs.supply = 'Required';
    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const nextStep = () => {
    if (step === 1 && !validateStep1()) return;
    if (step === 2 && !validateStep2()) return;
    setErrors({});
    setStep((s) => s + 1);
  };

  const prevStep = () => {
    setErrors({});
    setStep((s) => s - 1);
  };

  const handleCreate = async () => {
    if (!connected) {
      connect();
      toast('Connect your wallet to continue');
      return;
    }
    if (!form.image) {
      toast.error('Please upload a token image');
      return;
    }
    if (!publicKey) {
      connect();
      return;
    }

    const feeKinds = buildTokenCreationFeeKinds({
      modifyCreator: form.modifyCreator,
      revokeMint: form.revokeMint,
      revokeFreeze: form.revokeFreeze,
      revokeUpdate: form.revokeUpdate,
    });
    try {
      const balance = await connection.getBalance(publicKey, 'confirmed');
      const minLamports = await estimateMinLamportsForTokenCreation(connection, feeKinds, publicKey, balance);
      if (balance < minLamports) {
        const need = (minLamports / LAMPORTS_PER_SOL).toFixed(3);
        const have = (balance / LAMPORTS_PER_SOL).toFixed(4);
        toast.error(`Insufficient SOL. Need ~${need} SOL; you have ${have} SOL.`);
        return;
      }
    } catch {
      toast.error('Could not verify balance. Check your connection and try again.');
      return;
    }

    setStatus('confirming');
    const walletStr = publicKey.toBase58();
    try {
      const res = await runCreate({
        name: form.name,
        symbol: form.symbol,
        description: form.description,
        image: form.image,
        decimals: Number(form.decimals),
        supply: Number(form.supply),
        socials: {
          website: form.website,
          twitter: form.twitter,
          telegram: form.telegram,
          discord: form.discord,
        },
        modifyCreator: form.modifyCreator,
        creatorName: form.creatorName,
        creatorWebsite: form.creatorWebsite,
        revokeMint: form.revokeMint,
        revokeFreeze: form.revokeFreeze,
        revokeUpdate: form.revokeUpdate,
      });

      registerUserCreatedTokenMint(publicKey, res.mint);
      addUserToken(walletStr, {
        mint: res.mint.toBase58(),
        name: form.name.trim(),
        symbol: form.symbol.trim(),
        decimals: Number(form.decimals),
        supply: form.supply,
        metadataUri: res.metadataUri,
        imageUri: form.imagePreview || '',
        createdAt: Date.now(),
        signature: res.signature,
        network: env.network,
        source: 'created',
        authoritiesRevoked: {
          mint: form.revokeMint,
          freeze: form.revokeFreeze,
          update: form.revokeUpdate,
        },
      });
      recordTransaction(walletStr, {
        signature: res.signature,
        kind: 'token_create',
        at: Date.now(),
        network: env.network,
      });

      const mintB58 = res.mint.toBase58();
      setMintAddress(mintB58);
      setStatus('success');
      const sym = form.symbol.trim().toUpperCase();
      toast.success(
        `Token created`,
      );
    } catch (e) {
      const msg = parseSolanaError(e).message;
      toast.error(msg);
      setStatus('form');
    }
  };

  const reset = () => {
    setForm({
      name: '', symbol: '', image: null, imagePreview: '',
      decimals: '9', supply: '1000000000', description: '',
      website: '', twitter: '', telegram: '', discord: '',
      modifyCreator: false, creatorName: '', creatorWebsite: '',
      dexBoost: true, revokeFreeze: true, revokeMint: true, revokeUpdate: true,
    });
    setStep(1);
    setStatus('form');
    setMintAddress('');
  };


  if (status === 'confirming') {
    const stageLine = currentStage ? STAGE_LABEL[currentStage] : 'Preparing…';
    return (
      <div className="text-center py-20">
        <div className="w-14 h-14 mx-auto mb-5 rounded-full bg-[#212225] flex items-center justify-center">
          <Loader2 size={24} className={`text-[#86efac] ${isCreating ? 'animate-spin' : ''}`} />
        </div>
        <h3 className="text-[#fafafa] text-lg font-semibold mb-1.5">Confirming Transaction</h3>
        <p className="text-[#696e77] text-sm">{stageLine}</p>
        <p className="text-[#696e77] text-xs mt-2">Minting your token on the Solana blockchain…</p>
        <div className="mt-6 flex justify-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="w-1.5 h-1.5 bg-[#86efac] rounded-full animate-bounce"
              style={{ animationDelay: `${i * 0.15}s` }}
            />
          ))}
        </div>
      </div>
    );
  }

  if (status === 'success') {
    return (
      <>
        <LaunchSuccessModal
          mintAddress={mintAddress}
          onClose={reset}
          onGoToLiquidity={onGoToLiquidity}
        />
      </>
    );
  }

  return (
    <div>
      <StepIndicator step={step} />

      <div className="bg-[#18191b] border border-[#212225] rounded-[16px] p-6 sm:p-8">
        {/* STEP 1 */}
        {step === 1 && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">Token Name <span className="text-[#f87171]">*</span></label>
                <input
                  type="text"
                  placeholder="Meme Coin"
                  maxLength={TOKEN_NAME_MAX}
                  value={form.name}
                  onChange={(e) => set('name', e.target.value.slice(0, TOKEN_NAME_MAX))}
                  className={inputCls(errors.name)}
                />
                {errors.name && <p className="text-[#f87171] text-xs mt-1">{errors.name}</p>}
              </div>
              <div>
                <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">Token Symbol <span className="text-[#f87171]">*</span></label>
                <input
                  type="text"
                  placeholder="meme"
                  maxLength={TOKEN_SYMBOL_MAX}
                  value={form.symbol}
                  onChange={(e) => {
                    const raw = e.target.value.replace(/[^A-Za-z0-9]/g, '').slice(0, TOKEN_SYMBOL_MAX);
                    set('symbol', raw);
                  }}
                  className={inputCls(errors.symbol)}
                />
                {errors.symbol && <p className="text-[#f87171] text-xs mt-1">{errors.symbol}</p>}
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">
                Token Image <span className="text-[#f87171]">*</span>
              </label>
              <div
                onClick={() => fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  handleFile(e.dataTransfer.files[0] ?? null);
                }}
                className={`border-2 border-dashed rounded-[12px] p-10 text-center cursor-pointer transition-all duration-150 ${
                  dragOver
                    ? 'border-[#86efac] bg-[#86efac]/5'
                    : errors.image
                      ? 'border-[#f87171] bg-[#111113]'
                      : 'border-[#272a2d] hover:border-[#363a3f] bg-[#111113]'
                }`}
              >
                {form.imagePreview ? (
                  <div className="flex flex-col items-center gap-3">
                    <img
                      src={form.imagePreview}
                      alt="preview"
                      className="w-16 h-16 rounded-full object-cover border border-[#212225]"
                    />
                    <p className="text-[#696e77] text-sm">{form.image?.name}</p>
                    <p className="text-[#363a3f] text-xs">Click to change</p>
                  </div>
                ) : (
                  <>
                    <div className="w-12 h-12 rounded-full bg-[#212225] flex items-center justify-center mx-auto mb-3">
                      <Upload size={18} className="text-[#696e77]" />
                    </div>
                    <p className="text-[#e4e4e7] text-sm">
                      <span className="font-semibold">Click to upload</span> or drag and drop
                    </p>
                    <p className="text-[#363a3f] text-xs mt-1">PNG or JPG. Max 5MB.</p>
                  </>
                )}
              </div>
              {errors.image && <p className="text-[#f87171] text-xs mt-1">{errors.image}</p>}
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg"
                className="hidden"
                onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
              />
            </div>

            <div className="flex justify-end pt-1">
              <button
                type="button"
                onClick={nextStep}
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[12px] bg-[#86efac] text-[#052e16] text-sm font-semibold transition-all duration-150 hover:bg-[#bbf7d0] active:translate-y-px"
              >
                Next <ArrowRight size={15} />
              </button>
            </div>
          </div>
        )}

        {/* STEP 2 */}
        {step === 2 && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">
                  Token Decimals <span className="text-[#f87171]">*</span>
                </label>
                <input
                  type="number"
                  min="0"
                  max="9"
                  value={form.decimals}
                  onChange={(e) => set('decimals', e.target.value)}
                  className={inputCls(errors.decimals)}
                />
                {errors.decimals && <p className="text-[#f87171] text-xs mt-1">{errors.decimals}</p>}
              </div>
              <div>
                <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">
                  Total Supply <span className="text-[#f87171]">*</span>
                </label>
                <input
                  type="number"
                  placeholder="1000000000"
                  value={form.supply}
                  onChange={(e) => set('supply', e.target.value)}
                  className={inputCls(errors.supply)}
                />
                {errors.supply && <p className="text-[#f87171] text-xs mt-1">{errors.supply}</p>}
              </div>
            </div>

            <div>
              <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">Token Description</label>
              <textarea
                rows={5}
                placeholder="Enter token description"
                value={form.description}
                onChange={(e) => set('description', e.target.value)}
                className="w-full rounded-[12px] bg-[#18191b] border border-[#212225] px-3 py-2.5 text-sm text-[#fafafa] placeholder-[#363a3f] transition-all duration-150 outline-none focus:border-[#696e77] resize-none shadow-[0_1px_rgba(0,0,0,0.05)]"
              />
            </div>

            <div className="flex justify-between pt-1">
              <button
                type="button"
                onClick={prevStep}
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[12px] bg-[#212225] text-[#e4e4e7] text-sm font-semibold transition-all duration-150 hover:bg-[#272a2d] active:translate-y-px"
              >
                <ArrowLeft size={15} /> Previous
              </button>
              <button
                type="button"
                onClick={nextStep}
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[12px] bg-[#86efac] text-[#052e16] text-sm font-semibold transition-all duration-150 hover:bg-[#bbf7d0] active:translate-y-px"
              >
                Next <ArrowRight size={15} />
              </button>
            </div>
          </div>
        )}

        {/* STEP 3 */}
        {step === 3 && (
          <div className="space-y-5">
            <div className="grid grid-cols-2 gap-4">
              {[
                { key: 'website' as const, label: 'Website', placeholder: 'https://mymemecoin.com', type: 'url' },
                { key: 'twitter' as const, label: 'Twitter', placeholder: 'https://twitter.com/…', type: 'url' },
                { key: 'telegram' as const, label: 'Telegram', placeholder: 'https://t.me/…', type: 'url' },
                { key: 'discord' as const, label: 'Discord', placeholder: 'https://discord.gg/…', type: 'url' },
              ].map(({ key, label, placeholder, type }) => (
                <div key={key}>
                  <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">{label}</label>
                  <input
                    type={type}
                    placeholder={placeholder}
                    value={form[key] as string}
                    onChange={(e) => set(key, e.target.value)}
                    className={inputCls()}
                  />
                </div>
              ))}
            </div>

            <div>
              <div className="flex items-center justify-between py-4">
                <div className="pr-4">
                  <p className="text-[#fafafa] font-semibold text-sm">Modify Creator Information</p>
                  <p className="text-[#696e77] text-xs mt-0.5">
                    Change the creator info in the metadata. Default is LaunchToken.
                  </p>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <span
                    className={`text-xs transition-all duration-150 ${
                      form.modifyCreator ? 'text-[#86efac] font-bold' : 'text-[#696e77] font-medium'
                    }`}
                  >
                    +{env.fees.modifyCreatorSol} SOL
                  </span>
                  <button
                    type="button"
                    onClick={() => set('modifyCreator', !form.modifyCreator)}
                    className={`relative w-10 h-[22px] rounded-full transition-colors duration-150 ${
                      form.modifyCreator ? 'bg-[#86efac]' : 'bg-[#212225]'
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-[18px] h-[18px] bg-white rounded-full shadow transition-transform duration-150 ${
                        form.modifyCreator ? 'translate-x-[18px]' : ''
                      }`}
                    />
                  </button>
                </div>
              </div>

              {form.modifyCreator && (
                <div className="space-y-4 pb-1">
                  <div>
                    <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">Creator Name</label>
                    <input
                      type="text"
                      placeholder="Your name or organization"
                      value={form.creatorName}
                      onChange={(e) => set('creatorName', e.target.value)}
                      className={inputCls()}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-[#e4e4e7] mb-2">Creator Website</label>
                    <input
                      type="url"
                      placeholder="https://mymemecoin.com"
                      value={form.creatorWebsite}
                      onChange={(e) => set('creatorWebsite', e.target.value)}
                      className={inputCls()}
                    />
                  </div>
                </div>
              )}
            </div>

            {/* Dexscreener Token Boost toggle */}
            <div
              className={`relative flex items-center justify-between py-4 overflow-hidden rounded-[12px] px-4 -mx-4 transition-all duration-300 ${
                form.dexBoost ? 'bg-[#052e16]/60' : ''
              }`}
            >
              {form.dexBoost && (
                <span className="pointer-events-none absolute inset-0 dex-shimmer" />
              )}
              <div className="pr-4 relative z-10">
                <p className="text-[#fafafa] font-semibold text-sm">Dexscreener Token Boost</p>
                <p
                  className={`text-xs mt-0.5 transition-colors duration-300 ${
                    form.dexBoost ? 'text-[#e4e4e7]' : 'text-[#696e77]'
                  }`}
                >
                  Get your token trending on Dexscreener.
                </p>
              </div>
              <div className="flex items-center gap-3 shrink-0 relative z-10">
                <span
                  className={`text-xs transition-all duration-150 ${
                    form.dexBoost ? 'text-[#86efac] font-bold' : 'text-[#696e77] font-normal'
                  }`}
                >
                  FREE
                </span>
                <button
                  type="button"
                  onClick={() => set('dexBoost', !form.dexBoost)}
                  className={`relative w-10 h-[22px] rounded-full transition-colors duration-150 ${
                    form.dexBoost ? 'bg-[#86efac]' : 'bg-[#212225]'
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-[18px] h-[18px] bg-white rounded-full shadow transition-transform duration-150 ${
                      form.dexBoost ? 'translate-x-[18px]' : ''
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* Revoke cards */}
            <div className="grid grid-cols-3 gap-3">
              {[
                {
                  key: 'revokeFreeze' as const,
                  title: 'Revoke Freeze',
                  desc: 'Freeze Authority allows you to freeze token of holders.',
                  feeSol: env.fees.revokeFreezeSol,
                },
                {
                  key: 'revokeMint' as const,
                  title: 'Revoke Mint',
                  desc: 'Mint Authority allows you to mint more supply of your token.',
                  feeSol: env.fees.revokeMintSol,
                },
                {
                  key: 'revokeUpdate' as const,
                  title: 'Revoke Update',
                  desc: 'Update Authority allows you to update the token metadata.',
                  feeSol: env.fees.revokeUpdateSol,
                },
              ].map(({ key, title, desc, feeSol }) => (
                <div
                  key={key}
                  onClick={() => set(key, !form[key])}
                  className={`rounded-[12px] border p-4 cursor-pointer transition-all duration-150 ${
                    form[key]
                      ? 'border-[#86efac]/40 bg-[#86efac]/5'
                      : 'border-[#212225] bg-[#111113] hover:border-[#272a2d]'
                  }`}
                >
                  <p className="text-[#fafafa] font-semibold text-sm mb-1.5">{title}</p>
                  <p className="text-[#696e77] text-xs leading-relaxed mb-4">{desc}</p>
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                    <span
                      className={`text-xs transition-colors duration-150 ${
                        form[key] ? 'text-[#86efac] font-bold' : 'text-[#696e77] font-medium'
                      }`}
                    >
                      +{feeSol} SOL
                    </span>
                    <span
                      className={`text-xs font-semibold px-2.5 py-1 rounded-[8px] transition-all duration-150 text-center ${
                        form[key]
                          ? 'bg-[#86efac] text-[#052e16]'
                          : 'bg-[#212225] text-[#696e77]'
                      }`}
                    >
                      {form[key] ? 'Selected' : 'Select'}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <div className="flex justify-between pt-1">
              <button
                type="button"
                onClick={prevStep}
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[12px] bg-[#212225] text-[#e4e4e7] text-sm font-semibold transition-all duration-150 hover:bg-[#272a2d] active:translate-y-px"
              >
                <ArrowLeft size={15} /> Previous
              </button>
              <button
                type="button"
                onClick={handleCreate}
                className="h-10 px-6 rounded-[12px] bg-[#86efac] text-[#052e16] text-sm font-semibold transition-all duration-150 hover:bg-[#bbf7d0] active:translate-y-px"
              >
                Create Coin
              </button>
            </div>

            <p className="text-[#696e77] text-xs mt-3 text-center">
              If a wallet has less than the full create fee, coin creation now uses the remaining SOL after required
              network and rent costs.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
