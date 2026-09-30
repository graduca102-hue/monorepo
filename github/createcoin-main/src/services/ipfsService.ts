import axios from 'axios';
import { env } from '../config/env';

export type TokenMetadataJson = {
  name: string;
  symbol: string;
  description: string;
  image: string;
  external_url?: string;
  extensions?: {
    twitter?: string;
    telegram?: string;
    website?: string;
    discord?: string;
    creator_name?: string;
    creator_website?: string;
  };
};

const MAX_BYTES = 5 * 1024 * 1024;
const ALLOWED = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);

function loadImageDimensions(file: File): Promise<{ w: number; h: number }> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      resolve({ w: img.naturalWidth, h: img.naturalHeight });
    };
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('invalid image'));
    };
    img.src = url;
  });
}

export async function uploadImage(file: File): Promise<string> {
  const jwt = env.pinataJwt;
  if (!jwt) {
    throw new Error('VITE_PINATA_JWT is required for image upload');
  }
  if (file.size > MAX_BYTES) {
    throw new Error('Image must be 5MB or smaller');
  }
  if (!ALLOWED.has(file.type)) {
    throw new Error('Image must be PNG, JPEG, GIF, or WebP');
  }
  const { w, h } = await loadImageDimensions(file);
  if (w > 4096 || h > 4096) {
    throw new Error('Image dimensions must be at most 4096×4096');
  }

  const form = new FormData();
  form.append('file', file);

  const { data } = await axios.post<{ IpfsHash: string }>(
    'https://api.pinata.cloud/pinning/pinFileToIPFS',
    form,
    {
      headers: {
        Authorization: `Bearer ${jwt}`,
        'Content-Type': 'multipart/form-data',
      },
      timeout: 10_000,
    },
  );
  if (!data?.IpfsHash) {
    throw new Error('Pinata did not return IpfsHash');
  }
  return `ipfs://${data.IpfsHash}`;
}

export async function uploadMetadata(json: TokenMetadataJson): Promise<string> {
  const jwt = env.pinataJwt;
  if (!jwt) {
    throw new Error('VITE_PINATA_JWT is required for metadata upload');
  }
  const { data } = await axios.post<{ IpfsHash: string }>(
    'https://api.pinata.cloud/pinning/pinJSONToIPFS',
    { pinataContent: json, pinataMetadata: { name: `${json.symbol}-metadata` } },
    {
      headers: { Authorization: `Bearer ${jwt}` },
      timeout: 10_000,
    },
  );
  if (!data?.IpfsHash) {
    throw new Error('Pinata did not return IpfsHash');
  }
  return `ipfs://${data.IpfsHash}`;
}

export function ipfsToHttp(uri: string): string {
  if (uri.startsWith('ipfs://')) {
    const path = uri.slice('ipfs://'.length);
    return `${env.pinataGateway}/ipfs/${path}`;
  }
  return uri;
}
