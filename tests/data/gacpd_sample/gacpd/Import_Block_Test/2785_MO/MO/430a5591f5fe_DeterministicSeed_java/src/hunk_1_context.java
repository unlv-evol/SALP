     /**
      * Constructs a seed from a BIP 39 mnemonic code. See {@link MnemonicCode} for more
      * details on this scheme.
      * @param passphrase A user supplied passphrase, or an empty string if there is no passphrase
      * @param creationTimeSeconds When the seed was originally created, UNIX time.
      */
     public DeterministicSeed(byte[] entropy, String passphrase, long creationTimeSeconds) {
         checkArgument(entropy.length * 8 >= DEFAULT_SEED_ENTROPY_BITS, "entropy size too small");
         checkNotNull(passphrase);
 
         this.seed = MnemonicCode.toSeed(mnemonicCode, passphrase);
         this.encryptedMnemonicCode = null;
         this.encryptedSeed = null;
